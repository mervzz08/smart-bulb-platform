"""Outbound webhooks: push device events to subscriber URLs.

Each delivery is signed (same scheme as inbound, so receivers can verify it),
carries a stable X-Bridge-Delivery ID for de-duplication, and is retried with
exponential backoff on network errors, 5xx and 429. Other 4xx responses mean
"your request is wrong" and are not retried. Deliveries run concurrently, so
receivers should order events by their "seq" field rather than arrival time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from typing import Any, Awaitable, Callable

import httpx

from .config import Subscriber
from .signing import sign_header

log = logging.getLogger(__name__)


class WebhookDispatcher:
    def __init__(
        self,
        subscribers: tuple[Subscriber, ...],
        *,
        client: httpx.AsyncClient | None = None,
        max_attempts: int = 5,
        base_delay_s: float = 1.0,
        max_concurrency: int = 10,
        queue_size: int = 1000,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._subscribers = subscribers
        self._client = client
        self._owns_client = client is None
        self._max_attempts = max_attempts
        self._base_delay = base_delay_s
        self._sleep = sleep
        self._queue: asyncio.Queue[tuple[Subscriber, dict[str, Any]]] = asyncio.Queue(maxsize=queue_size)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[bool]] = set()
        self.delivered = 0
        self.failed = 0

    def enqueue(self, event: dict[str, Any]) -> None:
        for sub in self._subscribers:
            if not sub.wants(event["type"]):
                continue
            try:
                self._queue.put_nowait((sub, event))
            except asyncio.QueueFull:
                self.failed += 1
                log.error("webhook queue full; dropping %s for %s", event["type"], sub.url)

    async def run(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=5.0)
        while True:
            sub, event = await self._queue.get()
            await self._semaphore.acquire()
            task = asyncio.create_task(self._deliver_then_release(sub, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _deliver_then_release(self, sub: Subscriber, event: dict[str, Any]) -> bool:
        try:
            ok = await self.deliver(sub, event)
        finally:
            self._semaphore.release()
        if ok:
            self.delivered += 1
        else:
            self.failed += 1
        return ok

    async def deliver(self, sub: Subscriber, event: dict[str, Any]) -> bool:
        assert self._client is not None
        body = json.dumps(event, separators=(",", ":")).encode()
        delivery_id = str(uuid.uuid4())
        for attempt in range(1, self._max_attempts + 1):
            headers = {
                "Content-Type": "application/json",
                "X-Bridge-Event": event["type"],
                "X-Bridge-Delivery": delivery_id,
                # re-signed per attempt so a late retry is not outside the receiver's tolerance window
                "X-Bridge-Signature": sign_header(sub.secret, body),
            }
            try:
                response = await self._client.post(sub.url, content=body, headers=headers)
                if 200 <= response.status_code < 300:
                    return True
                if response.status_code < 500 and response.status_code != 429:
                    log.error("webhook %s rejected %s with %s; not retrying",
                              sub.url, delivery_id, response.status_code)
                    return False
                reason = f"HTTP {response.status_code}"
            except httpx.HTTPError as e:
                reason = type(e).__name__
            if attempt < self._max_attempts:
                delay = min(30.0, self._base_delay * 2 ** (attempt - 1)) * random.uniform(0.75, 1.25)
                log.warning("webhook %s attempt %d failed (%s); retrying in %.1fs",
                            sub.url, attempt, reason, delay)
                await self._sleep(delay)
        log.error("webhook %s gave up on %s after %d attempts", sub.url, delivery_id, self._max_attempts)
        return False

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
