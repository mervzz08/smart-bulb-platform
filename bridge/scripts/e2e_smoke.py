"""End-to-end check against a running stack (broker + bridge + 2 simulated
bulbs + webhook receiver). Exercises every integration path for real:
OAuth, API keys, REST command round-trips through MQTT, capability checks,
WebSocket events, signed inbound webhooks (valid / duplicate / tampered),
the flash-and-restore automation, signed outbound webhooks, and MQTT Last Will.

  python scripts/e2e_smoke.py [--kill-pid <pid> | --kill-cmd "docker compose kill sim-rgbw"]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import httpx
import websockets

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from simulators.shop_sim import build_event  # noqa: E402
from bulb_bridge.signing import sign_header  # noqa: E402

BASE = "http://127.0.0.1:8000"
RECEIVER = "http://127.0.0.1:9000"
READ_KEY = {"X-API-Key": "demo-read-key-0123456789"}
CLIENT = ("shop-backend", "demo-client-secret-0123456789")
SHOP_SECRET = "demo-shop-secret-0123456789"

passed = 0


def check(condition: bool, what: str, detail: object = "") -> None:
    global passed
    if not condition:
        print(f"FAIL  {what}  {detail}")
        raise SystemExit(1)
    passed += 1
    print(f"ok    {what}")


async def wait_for(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = await predicate()
            if last:
                return last
        except httpx.HTTPError as e:
            last = e
        await asyncio.sleep(0.25)
    check(False, what, f"(timed out; last={last!r})")


async def next_ws_event(ws, match, timeout: float):
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            event = json.loads(await asyncio.wait_for(ws.recv(), remaining))
        except asyncio.TimeoutError:
            break
        if match(event):
            return event
    return None


def signed_post(http: httpx.AsyncClient, event: dict, body: bytes | None = None):
    raw = json.dumps(event).encode()
    return http.post(f"{BASE}/webhooks/shop", content=body or raw,
                     headers={"Content-Type": "application/json", "X-Shop-Signature": sign_header(SHOP_SECRET, raw)})


async def main(kill_pid: int | None, kill_cmd: str | None) -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        async def stack_ready():
            health = (await http.get(f"{BASE}/health")).json()
            if not health["mqtt_connected"]:
                return None
            devices = (await http.get(f"{BASE}/devices", headers=READ_KEY)).json()
            online = {d["id"] for d in devices if d["online"] and d["capabilities"]}
            return online >= {"sim-rgbw", "sim-rgbcct"}

        await wait_for(stack_ready, 30, "bridge connected and both simulated bulbs discovered")
        check(True, "bridge connected and both simulated bulbs discovered")

        r = await http.post(f"{BASE}/oauth/token", data={"grant_type": "client_credentials"}, auth=CLIENT)
        check(r.status_code == 200, "OAuth client-credentials token issued", r.text)
        token = r.json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        r = await http.post(f"{BASE}/devices/sim-rgbcct/commands", headers=auth,
                            json={"color_temp": 370, "brightness": 140})
        check(r.status_code == 200 and r.json()["state"]["color_temp"] == 370,
              "REST command -> MQTT -> bulb -> state confirmed back", r.text)

        r = await http.post(f"{BASE}/devices/sim-rgbw/commands", headers=auth, json={"color_temp": 300})
        check(r.status_code == 422, "unsupported command rejected before reaching the bulb", r.text)

        r = await http.post(f"{BASE}/devices/sim-rgbw/commands", headers=READ_KEY, json={"state": "OFF"})
        check(r.status_code == 403, "read-only API key cannot send commands")

        async with websockets.connect(f"ws://127.0.0.1:8000/ws") as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            snapshot = json.loads(await ws.recv())
            check(snapshot["type"] == "snapshot" and len(snapshot["devices"]) >= 2, "WebSocket auth + snapshot")

            event = build_event("order.paid")
            r = await signed_post(http, event)
            check(r.status_code == 200 and r.json()["status"] == "processed", "signed order.paid webhook accepted", r.text)

            flash = await next_ws_event(ws, lambda e: e["type"] == "device.state" and e["device_id"] == "sim-rgbcct"
                                        and e["data"]["effect"] == "pulse", 5)
            check(flash is not None and flash["data"]["color"] == {"r": 0, "g": 255, "b": 0},
                  "bulb flashed green (seen live over WebSocket)", flash)

            r = await signed_post(http, event)
            check(r.json()["status"] == "duplicate", "redelivered webhook acknowledged, not re-run")

            tampered = json.dumps(build_event("order.paid")).encode()
            r = await signed_post(http, build_event("order.paid"), body=tampered)
            check(r.status_code == 401, "tampered webhook rejected", r.text)

            restored = await next_ws_event(ws, lambda e: e["type"] == "device.state" and e["device_id"] == "sim-rgbcct"
                                           and e["data"]["effect"] == "none", 10)
            check(restored is not None and restored["data"]["color_temp"] == 370
                  and restored["data"]["color_mode"] == "color_temp" and restored["data"]["brightness"] == 140,
                  "bulb restored to its exact previous state after the alert", restored)

            if kill_pid or kill_cmd:
                if kill_pid:
                    os.kill(kill_pid, signal.SIGTERM)
                else:
                    subprocess.run(kill_cmd, shell=True, check=True)
                offline = await next_ws_event(ws, lambda e: e["type"] == "device.availability"
                                              and e["device_id"] == "sim-rgbw" and not e["data"]["online"], 15)
                check(offline is not None, "bulb going away is reported offline (availability topic)", offline)

        async def receiver_got_events():
            stats = (await http.get(f"{RECEIVER}/stats")).json()
            return stats if stats["by_type"].get("device.state", 0) >= 3 else None

        stats = await wait_for(receiver_got_events, 10, "outbound webhooks delivered")
        check(stats["rejected"] == 0, "outbound webhooks delivered and signature-verified by receiver", stats)

    print(f"\nall {passed} end-to-end checks passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--kill-pid", type=int, help="pid of the sim-rgbw process to stop")
    parser.add_argument("--kill-cmd", help="shell command that stops sim-rgbw (e.g. docker compose kill sim-rgbw)")
    args = parser.parse_args()
    asyncio.run(main(args.kill_pid, args.kill_cmd))
