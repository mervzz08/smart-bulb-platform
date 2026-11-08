"""E-commerce automations: a shop event (e.g. order.paid) drives a bulb.

A rule with duration_s is temporary: the bulb shows the alert, then goes back
to exactly how it was. The subtle part is overlapping alerts. If a second
order arrives mid-flash, the restore must go back to the state *before the
first flash*, not to the flashing state, so the snapshot is taken only once
per alert window.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .config import Rule
from .models import LightCommand
from .registry import Registry

log = logging.getLogger(__name__)


class Publisher(Protocol):
    def publish_command(self, device_id: str, payload: dict[str, Any]) -> bool: ...


def state_to_command(state: dict[str, Any], transition: float = 0.5) -> dict[str, Any]:
    """Turn a reported state back into a command that recreates it."""
    cmd: dict[str, Any] = {"transition": transition}
    mode = state.get("color_mode")
    color = state.get("color")
    if mode == "rgb" and isinstance(color, dict):
        cmd["color"] = {k: color[k] for k in ("r", "g", "b")}
    elif mode == "color_temp" and "color_temp" in state:
        cmd["color_temp"] = state["color_temp"]
    if mode == "white":
        cmd["white"] = state.get("brightness", 255)
    elif state.get("brightness"):
        cmd["brightness"] = state["brightness"]
    cmd["effect"] = state.get("effect", "none")
    cmd["state"] = "OFF" if state.get("state") == "OFF" else "ON"
    return LightCommand.model_validate(cmd).to_payload()


class AutomationEngine:
    def __init__(self, rules: tuple[Rule, ...], registry: Registry, publisher: Publisher,
                 restore_transition_s: float = 0.5) -> None:
        self._rules = rules
        self._registry = registry
        self._publisher = publisher
        self._restore_transition = restore_transition_s
        self._snapshots: dict[str, dict[str, Any] | None] = {}
        self._restore_tasks: dict[str, asyncio.Task[None]] = {}

    def has_rules_for(self, event_type: str) -> bool:
        return any(r.event == event_type for r in self._rules)

    def handle(self, event_type: str) -> list[dict[str, Any]]:
        """Run every rule for this event. Must be called on the event loop."""
        actions = []
        for rule in self._rules:
            if rule.event != event_type:
                continue
            temporary = rule.duration_s is not None
            first_in_window = rule.device not in self._snapshots
            if temporary and first_in_window:
                device = self._registry.get(rule.device)
                self._snapshots[rule.device] = dict(device.state) if device and device.state else None

            sent = self._publisher.publish_command(rule.device, rule.command.to_payload())
            actions.append({"device": rule.device, "sent": sent})

            if temporary:
                if sent:
                    self._schedule_restore(rule.device, rule.duration_s)
                elif first_in_window:
                    self._snapshots.pop(rule.device, None)
        return actions

    def _schedule_restore(self, device_id: str, delay_s: float) -> None:
        previous = self._restore_tasks.get(device_id)
        if previous:
            previous.cancel()  # extend the window; keep the original snapshot
        self._restore_tasks[device_id] = asyncio.get_running_loop().create_task(
            self._restore_later(device_id, delay_s))

    async def _restore_later(self, device_id: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
        except asyncio.CancelledError:
            return
        if self._restore_tasks.get(device_id) is asyncio.current_task():
            del self._restore_tasks[device_id]
        snapshot = self._snapshots.pop(device_id, None)
        if snapshot is None:
            log.warning("no prior state known for %s; leaving it as is", device_id)
            return
        if not self._publisher.publish_command(device_id, state_to_command(snapshot, self._restore_transition)):
            log.warning("could not restore %s: MQTT unavailable", device_id)

    def cancel_all(self) -> None:
        for task in self._restore_tasks.values():
            task.cancel()
        self._restore_tasks.clear()
