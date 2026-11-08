"""In-memory view of every bulb the bridge has heard from, plus an event bus.

All mutation happens on the asyncio event loop thread (MQTT callbacks are
handed over with call_soon_threadsafe), so no locks are needed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

Event = dict[str, Any]


@dataclass
class Device:
    id: str
    online: bool = False
    state: dict[str, Any] | None = None
    capabilities: dict[str, Any] = field(default_factory=dict)
    last_seen: float | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Registry:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._devices: dict[str, Device] = {}
        self._queues: set[asyncio.Queue[Event]] = set()
        self._listeners: list[Callable[[Event], None]] = []
        self._seq = 0
        self._clock = clock

    # ---- queries ---------------------------------------------------------
    def get(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    def all(self) -> list[Device]:
        return sorted(self._devices.values(), key=lambda d: d.id)

    # ---- updates (from MQTT) --------------------------------------------
    def _device(self, device_id: str) -> Device:
        device = self._devices.get(device_id)
        if device is None:
            device = self._devices[device_id] = Device(device_id)
        device.last_seen = self._clock()
        return device

    def set_state(self, device_id: str, state: dict[str, Any]) -> None:
        self._device(device_id).state = state
        self._emit("device.state", device_id, state)

    def set_availability(self, device_id: str, online: bool) -> None:
        device = self._device(device_id)
        if device.online != online:
            device.online = online
            self._emit("device.availability", device_id, {"online": online})

    def set_capabilities(self, device_id: str, capabilities: dict[str, Any]) -> None:
        self._device(device_id).capabilities = capabilities
        self._emit("device.discovered", device_id, capabilities)

    def record_error(self, device_id: str, message: str) -> None:
        self._device(device_id).last_error = message
        self._emit("device.error", device_id, {"error": message})

    # ---- event bus -------------------------------------------------------
    def add_listener(self, fn: Callable[[Event], None]) -> None:
        self._listeners.append(fn)

    @contextlib.contextmanager
    def watch(self, maxsize: int = 100) -> Iterator[asyncio.Queue[Event]]:
        """Queue of every event emitted while the block is active."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._queues.add(queue)
        try:
            yield queue
        finally:
            self._queues.discard(queue)

    def _emit(self, event_type: str, device_id: str, data: dict[str, Any]) -> None:
        self._seq += 1
        event: Event = {"seq": self._seq, "type": event_type, "device_id": device_id,
                        "ts": self._clock(), "data": data}
        for queue in list(self._queues):
            if queue.full():  # slow consumer: drop its oldest event, never block the bridge
                queue.get_nowait()
            queue.put_nowait(event)
        for listener in self._listeners:
            try:
                listener(event)
            except Exception:  # one bad listener must not break the others
                log.exception("event listener failed")


async def next_event(queue: asyncio.Queue[Event], device_id: str, types: set[str],
                     timeout: float) -> Event | None:
    """Wait for the next matching event, or None on timeout."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while (remaining := deadline - loop.time()) > 0:
        try:
            event = await asyncio.wait_for(queue.get(), remaining)
        except asyncio.TimeoutError:
            return None
        if event["device_id"] == device_id and event["type"] in types:
            return event
    return None
