"""HMAC-SHA256 webhook signatures, used for both directions.

Header format:  X-...-Signature: t=<unix seconds>,v1=<hex digest>
Signed message: "<t>.<raw request body>"

Putting the timestamp inside the signed message means a captured request
cannot be replayed later: changing t breaks the signature, and an old t is
rejected by the tolerance window. Several v1 values may be present, and
several secrets may be configured, so secrets can be rotated with no downtime.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Sequence


class SignatureError(Exception):
    pass


def compute(secret: str, timestamp: int, body: bytes) -> str:
    message = str(timestamp).encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def sign_header(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    return f"t={ts},v1={compute(secret, ts, body)}"


def verify(
    secrets: Sequence[str],
    body: bytes,
    header: str | None,
    *,
    tolerance_s: int = 300,
    now: float | None = None,
) -> int:
    """Return the signed timestamp, or raise SignatureError."""
    if not header:
        raise SignatureError("missing signature header")

    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, sep, value = part.strip().partition("=")
        if not sep:
            continue
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            signatures.append(value)
    if timestamp is None or not signatures:
        raise SignatureError("malformed signature header")

    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_s:
        raise SignatureError("timestamp outside tolerance")

    for secret in secrets:
        expected = compute(secret, timestamp, body)
        # compare_digest takes the same time whether the first or last byte
        # differs, so an attacker cannot guess the signature byte by byte.
        if any(hmac.compare_digest(expected, sig) for sig in signatures):
            return timestamp
    raise SignatureError("signature mismatch")
