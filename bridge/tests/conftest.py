from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from bulb_bridge.auth import sha256_hex
from bulb_bridge.config import load_settings
from bulb_bridge.main import create_app
from bulb_bridge.registry import Registry
from simulators.bulb_model import VARIANTS, CommandError, apply_command, default_state, to_wire

JWT_SECRET = "test-jwt-secret-that-is-long-enough-0123456789"
SHOP_SECRET = "shop-secret-0123456789"
READ_KEY = "read-key-0123456789"
WRITE_KEY = "write-key-0123456789"
CLIENT_ID = "shop-backend"
CLIENT_SECRET = "client-secret-0123456789"

BASE_ENV = {
    "BRIDGE_JWT_SECRET": JWT_SECRET,
    "BRIDGE_SHOP_WEBHOOK_SECRETS": SHOP_SECRET,
    "BRIDGE_API_KEYS": json.dumps([
        {"name": "dashboard", "sha256": sha256_hex(READ_KEY), "scopes": ["devices:read", "events:read"]},
        {"name": "ops", "sha256": sha256_hex(WRITE_KEY), "scopes": ["devices:read", "devices:write"]},
    ]),
    "BRIDGE_OAUTH_CLIENTS": json.dumps([
        {"client_id": CLIENT_ID, "secret_sha256": sha256_hex(CLIENT_SECRET),
         "scopes": ["devices:read", "devices:write", "events:read"]},
    ]),
    "BRIDGE_RULES": json.dumps([
        {"event": "order.paid", "device": "cct-1",
         "command": {"color": {"r": 0, "g": 255, "b": 0}, "effect": "pulse"}, "duration_s": 30},
    ]),
    "BRIDGE_COMMAND_WAIT_S": "0.3",
}


class FakeBridge:
    """Stands in for MqttBridge. When `simulate_devices` is on, published
    commands are applied with the same rules as the firmware and the new state
    is reported back through the registry, like a real bulb would."""

    def __init__(self, settings, registry: Registry) -> None:
        self.registry = registry
        self.connected = True
        self.simulate_devices = True
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.states: dict[str, dict[str, Any]] = {}
        self.variants: dict[str, str] = {}

    def start(self, loop) -> None:
        self.loop = loop

    def stop(self) -> None:
        pass

    def publish_command(self, device_id: str, payload: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        self.sent.append((device_id, payload))
        if self.simulate_devices and self.loop and device_id in self.variants:
            self.loop.call_soon(self._device_reply, device_id, payload)
        return True

    def _device_reply(self, device_id: str, payload: dict[str, Any]) -> None:
        caps = VARIANTS[self.variants[device_id]]
        try:
            self.states[device_id], _ = apply_command(self.states[device_id], payload, caps)
        except CommandError as e:
            self.registry.record_error(device_id, str(e))
            return
        self.registry.set_state(device_id, to_wire(self.states[device_id], caps))

    def add_device(self, device_id: str, variant: str) -> None:
        caps = VARIANTS[variant]
        self.variants[device_id] = variant
        self.states[device_id] = default_state(caps)
        modes = ["rgb"] + (["color_temp"] if caps.tunable else ["white"])
        self.registry.set_capabilities(device_id, {"supported_color_modes": modes,
                                                   "effect_list": ["none", "pulse"]})
        self.registry.set_availability(device_id, True)
        self.registry.set_state(device_id, to_wire(self.states[device_id], caps))


@pytest.fixture
def settings():
    return load_settings(BASE_ENV)


@pytest.fixture
def webhook_log() -> list[httpx.Request]:
    return []


@pytest.fixture
def make_client(settings, webhook_log) -> Callable[..., TestClient]:
    clients: list[TestClient] = []

    def factory(**overrides) -> TestClient:
        def handler(request: httpx.Request) -> httpx.Response:
            webhook_log.append(request)
            return httpx.Response(200)

        app = create_app(replace(settings, **overrides), bridge_factory=FakeBridge,
                         webhook_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        client = TestClient(app)
        client.__enter__()  # runs the lifespan (starts the fake bridge)
        bridge: FakeBridge = app.state.bridge
        bridge.add_device("rgbw-1", "rgbw")
        bridge.add_device("cct-1", "rgbcct")
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def bearer(client: TestClient, scope: str | None = None) -> dict[str, str]:
    data = {"grant_type": "client_credentials"}
    if scope:
        data["scope"] = scope
    r = client.post("/oauth/token", data=data, auth=(CLIENT_ID, CLIENT_SECRET))
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
