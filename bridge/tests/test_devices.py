import pytest

from .conftest import WRITE_KEY

H = {"X-API-Key": WRITE_KEY}


def test_list_and_get_devices(client):
    devices = client.get("/devices", headers=H).json()
    assert [d["id"] for d in devices] == ["cct-1", "rgbw-1"]
    assert client.get("/devices/cct-1", headers=H).json()["online"] is True
    assert client.get("/devices/nope", headers=H).status_code == 404


def test_command_is_confirmed_by_device_state(client):
    r = client.post("/devices/cct-1/commands", json={"color_temp": 370, "brightness": 128}, headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["state"]["color_temp"] == 370 and body["state"]["color_mode"] == "color_temp"
    assert client.app.state.bridge.sent[-1] == ("cct-1", {"color_temp": 370, "brightness": 128})


def test_unconfirmed_command_returns_202(client):
    client.app.state.bridge.simulate_devices = False  # bulb never answers
    r = client.post("/devices/cct-1/commands", json={"state": "OFF"}, headers=H)
    assert r.status_code == 202 and r.json() == {"status": "sent", "confirmed": False}


def test_fire_and_forget(client):
    r = client.post("/devices/cct-1/commands?wait=false", json={"state": "OFF"}, headers=H)
    assert r.status_code == 202 and r.json() == {"status": "sent"}


def test_capability_check_happens_before_sending(client):
    r = client.post("/devices/rgbw-1/commands", json={"color_temp": 300}, headers=H)
    assert r.status_code == 422 and "color_temp" in r.json()["detail"]
    assert client.app.state.bridge.sent == []


def test_device_side_rejection_is_reported(client):
    # Capabilities unknown -> the bridge forwards it, the bulb itself refuses.
    client.app.state.registry.get("rgbw-1").capabilities = {}
    r = client.post("/devices/rgbw-1/commands", json={"color_temp": 300}, headers=H)
    assert r.status_code == 422
    assert "this bulb has no tunable white" in r.json()["detail"]


def test_offline_device_and_broker_down(client):
    client.app.state.registry.get("rgbw-1").online = False
    assert client.post("/devices/rgbw-1/commands", json={"state": "ON"}, headers=H).status_code == 409
    client.app.state.bridge.connected = False
    assert client.post("/devices/cct-1/commands", json={"state": "ON"}, headers=H).status_code == 503


@pytest.mark.parametrize("payload", [
    {},
    {"brightness": 256},
    {"color": {"r": 1, "g": 2}},
    {"color": {"r": 1, "g": 2, "b": 3}, "color_temp": 200},
    {"effect": "disco"},
    {"transition": -1},
    {"state": "on"},
    {"unknown": 1},
])
def test_invalid_commands_are_rejected_at_the_edge(client, payload):
    assert client.post("/devices/cct-1/commands", json=payload, headers=H).status_code == 422
    assert client.app.state.bridge.sent == []


def test_health_is_public(client):
    assert client.get("/health").json() == {"status": "ok", "mqtt_connected": True, "devices": 2}
