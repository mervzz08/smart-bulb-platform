import json
import ssl

import pytest
from starlette.websockets import WebSocketDisconnect

from bulb_bridge.mqtt_bridge import MqttBridge, build_tls_context
from bulb_bridge.registry import Registry
from simulators.bulb_model import VARIANTS, CommandError, apply_command, default_state, to_wire

from .conftest import READ_KEY, WRITE_KEY, bearer

# ---- WebSocket -----------------------------------------------------------------


def test_ws_snapshot_then_live_events(client):
    token = bearer(client)["Authorization"].split()[1]
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "token": token})
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot"
        assert {d["id"] for d in snapshot["devices"]} == {"cct-1", "rgbw-1"}

        client.post("/devices/cct-1/commands", json={"brightness": 42}, headers={"X-API-Key": WRITE_KEY})
        event = ws.receive_json()
        assert event["type"] == "device.state" and event["device_id"] == "cct-1"
        assert event["data"]["brightness"] == 42


def test_ws_accepts_api_key_with_scope(client):
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "auth", "api_key": READ_KEY})
        assert ws.receive_json()["type"] == "snapshot"


@pytest.mark.parametrize("hello, code", [
    ({"type": "auth", "token": "not-a-jwt"}, 4401),
    ({"type": "hello"}, 4401),
    ({"type": "auth", "api_key": WRITE_KEY}, 4403),  # valid key, but no events:read scope
])
def test_ws_rejects_bad_auth(client, hello, code):
    with client.websocket_connect("/ws") as ws:
        ws.send_json(hello)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == code


# ---- MQTT message handling -------------------------------------------------------


@pytest.fixture
def mqtt_bridge(settings):
    registry = Registry()
    return MqttBridge(settings, registry), registry


def test_discovery_state_availability_error(mqtt_bridge):
    bridge, registry = mqtt_bridge
    bridge.handle_message("homeassistant/light/a1b2c3/config", json.dumps({
        "supported_color_modes": ["rgb", "color_temp"], "min_mireds": 153, "max_mireds": 370,
        "effect_list": ["none", "pulse"], "device": {"model": "RGBCCT", "sw_version": "1.0.0"},
        "command_topic": "ignored"}).encode())
    bridge.handle_message("bulb/a1b2c3/availability", b"online")
    bridge.handle_message("bulb/a1b2c3/state", b'{"state":"ON","brightness":9}')
    bridge.handle_message("bulb/a1b2c3/error", b"effect must be none or pulse")

    d = registry.get("a1b2c3")
    assert d.capabilities == {"supported_color_modes": ["rgb", "color_temp"], "min_mireds": 153,
                              "max_mireds": 370, "effect_list": ["none", "pulse"],
                              "model": "RGBCCT", "sw_version": "1.0.0"}
    assert d.online and d.state == {"state": "ON", "brightness": 9}
    assert d.last_error == "effect must be none or pulse"

    bridge.handle_message("bulb/a1b2c3/availability", b"offline")  # LWT from the broker
    assert not registry.get("a1b2c3").online


@pytest.mark.parametrize("topic, payload", [
    ("bulb/a1b2c3/state", b"{not json"),
    ("bulb/a1b2c3/state", b"[1,2]"),
    ("bulb/a1b2c3/state", b"\xff\xfe"),
    ("bulb/bad id!/state", b"{}"),
    ("bulb/a1b2c3/extra/state", b"{}"),
    ("other/a1b2c3/state", b"{}"),
    ("homeassistant/light/a1b2c3/config", b""),  # removal, not a new device
])
def test_bad_or_foreign_messages_are_ignored(mqtt_bridge, topic, payload):
    bridge, registry = mqtt_bridge
    bridge.handle_message(topic, payload)
    assert registry.all() == []


def test_publish_refused_while_disconnected(mqtt_bridge):
    bridge, _ = mqtt_bridge
    assert bridge.publish_command("a1b2c3", {"state": "ON"}) is False


def test_tls_context_is_strict():
    ctx = build_tls_context(None)
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED


# ---- simulator parity with firmware rules ------------------------------------------


def test_simulator_matches_firmware_semantics():
    caps = VARIANTS["rgbcct"]
    s = default_state(caps)
    s2, ms = apply_command(s, {"brightness": 0}, caps)
    assert not s2["on"] and s2["brightness"] == 255 and ms == 300
    s3, _ = apply_command(s, {"color_temp": 9999}, caps)
    assert s3["mireds"] == 370
    with pytest.raises(CommandError, match="effect"):
        apply_command(s, {"brightness": 10, "effect": "disco"}, caps)
    assert s["brightness"] == 255  # untouched
    with pytest.raises(CommandError, match="tunable"):
        apply_command(default_state(VARIANTS["rgbw"]), {"color_temp": 300}, VARIANTS["rgbw"])
    assert to_wire(s, caps)["color_mode"] == "color_temp"
