"""Remembers webhook event IDs so a retried delivery is not processed twice.

Webhook senders retry whenever they do not see a 2xx in time, so duplicates
are normal, not exceptional. This store is in-memory and per-process; running
several bridge replicas would need a shared store (e.g. Redis SET NX EX).
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable


class IdempotencyStore:
    def __init__(self, ttl_s: float = 86400, max_items: int = 10000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_s
        self._max = max_items
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()

    def _purge(self) -> None:
        cutoff = self._clock() - self._ttl
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)

    def seen(self, key: str) -> bool:
        self._purge()
        return key in self._seen

    def add(self, key: str) -> None:
        self._seen[key] = self._clock()
        self._seen.move_to_end(key)
        self._purge()
