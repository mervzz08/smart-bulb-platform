"""Example subscriber for the bridge's outbound webhooks. Verifies each
signature, drops duplicate deliveries, and prints the event.

  python -m simulators.webhook_receiver --port 9000 --secret "$SUBSCRIBER_SECRET"

GET /stats returns counters (used by the end-to-end test).
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bulb_bridge.signing import SignatureError, verify


def make_handler(secret: str):
    stats = {"verified": 0, "rejected": 0, "duplicates": 0, "by_type": {}}
    seen: set[str] = set()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            self._reply(200, stats) if self.path == "/stats" else self._reply(404, {})

        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                verify([secret], body, self.headers.get("X-Bridge-Signature"))
            except SignatureError as e:
                stats["rejected"] += 1
                self._reply(401, {"error": str(e)})
                return
            delivery = self.headers.get("X-Bridge-Delivery", "")
            if delivery in seen:
                stats["duplicates"] += 1
                self._reply(200, {"status": "duplicate"})
                return
            seen.add(delivery)
            event = json.loads(body)
            stats["verified"] += 1
            stats["by_type"][event["type"]] = stats["by_type"].get(event["type"], 0) + 1
            print(f"verified {event['type']} #{event['seq']} from {event['device_id']}: {event['data']}", flush=True)
            self._reply(200, {"status": "ok"})

        def log_message(self, *args) -> None:  # keep output to the events themselves
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--secret", required=True)
    args = parser.parse_args()
    print(f"listening on :{args.port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.secret)).serve_forever()


if __name__ == "__main__":
    main()
