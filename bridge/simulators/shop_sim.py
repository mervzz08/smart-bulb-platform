"""Mock e-commerce platform: sends a signed order webhook to the bridge.

  python -m simulators.shop_sim --url http://localhost:8000/webhooks/shop \
      --secret "$SHOP_SECRET" --type order.paid
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

import httpx

from bulb_bridge.signing import sign_header


def build_event(event_type: str, event_id: str | None = None) -> dict:
    return {
        "id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "type": event_type,
        "created_at": int(time.time()),
        "data": {"order_id": f"ord_{uuid.uuid4().hex[:8]}", "total": "49.00", "currency": "USD"},
    }


def send(url: str, secret: str, event: dict, *, tamper: bool = False) -> httpx.Response:
    body = json.dumps(event, separators=(",", ":")).encode()
    signature = sign_header(secret, body)
    if tamper:
        body = body.replace(b"49.00", b"0.01")  # signed one thing, sent another
    return httpx.post(url, content=body, timeout=10,
                      headers={"Content-Type": "application/json", "X-Shop-Signature": signature})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000/webhooks/shop")
    parser.add_argument("--secret", required=True)
    parser.add_argument("--type", default="order.paid")
    parser.add_argument("--id", help="reuse an event id to test duplicate handling")
    parser.add_argument("--tamper", action="store_true", help="alter the body after signing")
    args = parser.parse_args()

    response = send(args.url, args.secret, build_event(args.type, args.id), tamper=args.tamper)
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
