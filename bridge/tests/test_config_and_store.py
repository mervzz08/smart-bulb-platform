import json

import pytest

from bulb_bridge.config import ConfigError, load_settings
from bulb_bridge.idempotency import IdempotencyStore

from .conftest import BASE_ENV


def env(**changes):
    e = dict(BASE_ENV)
    e.update(changes)
    return e


def test_valid_config_loads(settings):
    assert len(settings.api_keys) == 2
    assert settings.rules[0].event == "order.paid"
    assert settings.mqtt_port == 1883


def test_tls_defaults_to_port_8883():
    assert load_settings(env(MQTT_TLS="true")).mqtt_port == 8883


@pytest.mark.parametrize("changes, message", [
    ({"BRIDGE_JWT_SECRET": "short"}, "JWT_SECRET"),
    ({"BRIDGE_SHOP_WEBHOOK_SECRETS": ""}, "SHOP_WEBHOOK"),
    ({"BRIDGE_API_KEYS": "{not json"}, "not valid JSON"),
    ({"BRIDGE_API_KEYS": json.dumps([{"sha256": "abc", "scopes": []}])}, "sha256"),
    ({"BRIDGE_OAUTH_CLIENTS": json.dumps([{"client_id": "x", "secret_sha256": "0" * 64,
                                           "scopes": ["admin"]}])}, "unknown scopes"),
    ({"BRIDGE_RULES": json.dumps([{"event": "order.paid", "device": "d",
                                   "command": {"brightness": 999}}])}, "invalid command"),
    ({"BRIDGE_WEBHOOK_SUBSCRIBERS": json.dumps([{"url": "ftp://x", "secret": "s" * 16}])}, "http"),
])
def test_bad_config_fails_fast(changes, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(env(**changes))


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_idempotency_store_remembers_until_ttl():
    clock = FakeClock()
    store = IdempotencyStore(ttl_s=60, clock=clock)
    assert not store.seen("evt_1")
    store.add("evt_1")
    clock.t = 59
    assert store.seen("evt_1")
    clock.t = 61
    assert not store.seen("evt_1")


def test_idempotency_store_is_bounded():
    store = IdempotencyStore(max_items=3, clock=FakeClock())
    for i in range(5):
        store.add(f"evt_{i}")
    assert not store.seen("evt_0") and not store.seen("evt_1")
    assert store.seen("evt_4")
