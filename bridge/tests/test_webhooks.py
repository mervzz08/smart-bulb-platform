import asyncio
import json
import time

import httpx
import pytest

from bulb_bridge.config import Subscriber
from bulb_bridge.signing import sign_header, verify
from bulb_bridge.webhooks_out import WebhookDispatcher

from .conftest import SHOP_SECRET

# ---- inbound -------------------------------------------------------------------


def post_event(client, event, *, secret=SHOP_SECRET, timestamp=None, body=None):
    raw = body if body is not None else json.dumps(event).encode()
    return client.post("/webhooks/shop", content=raw, headers={
        "Content-Type": "application/json",
        "X-Shop-Signature": sign_header(secret, json.dumps(event).encode(), timestamp),
    })


ORDER = {"id": "evt_1", "type": "order.paid", "data": {"order_id": "ord_1"}}


def test_signed_event_triggers_rule(client):
    r = post_event(client, ORDER)
    assert r.status_code == 200
    assert r.json() == {"status": "processed", "actions": [{"device": "cct-1", "sent": True}]}
    assert client.app.state.bridge.sent[-1] == ("cct-1", {"color": {"r": 0, "g": 255, "b": 0},
                                                         "effect": "pulse"})


def test_duplicate_delivery_is_acknowledged_not_reprocessed(client):
    post_event(client, ORDER)
    sent_before = len(client.app.state.bridge.sent)
    r = post_event(client, ORDER)
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    assert len(client.app.state.bridge.sent) == sent_before


def test_bad_signatures(client):
    assert post_event(client, ORDER, secret="wrong-secret-000000").status_code == 401
    assert post_event(client, ORDER, timestamp=int(time.time()) - 3600).status_code == 401
    tampered = json.dumps({**ORDER, "type": "order.refunded"}).encode()
    assert post_event(client, ORDER, body=tampered).status_code == 401
    r = client.post("/webhooks/shop", json=ORDER)
    assert r.status_code == 401 and r.json()["error"] == "missing signature header"
    assert client.app.state.bridge.sent == []


def test_event_without_rule_is_ignored(client):
    r = post_event(client, {"id": "evt_2", "type": "customer.created"})
    assert r.json()["status"] == "ignored"


def test_broker_outage_asks_sender_to_retry_and_retry_works(client):
    client.app.state.bridge.connected = False
    r = post_event(client, ORDER)
    assert r.status_code == 503  # not marked processed...
    client.app.state.bridge.connected = True
    assert post_event(client, ORDER).json()["status"] == "processed"  # ...so the retry runs


def test_invalid_and_oversized_payloads(client):
    bad = {"type": "order.paid"}  # no id
    assert post_event(client, bad).status_code == 400
    huge = {"id": "evt_3", "type": "order.paid", "data": {"x": "a" * 70000}}
    assert post_event(client, huge).status_code == 413


# ---- outbound ------------------------------------------------------------------

SUB = Subscriber("https://example.test/hook", "subscriber-secret-01", frozenset({"device.state"}))
EVENT = {"seq": 1, "type": "device.state", "device_id": "cct-1", "ts": 0, "data": {"state": "ON"}}


async def no_sleep(_):
    pass


def run_delivery(responses):
    calls = []

    def handler(request):
        calls.append(request)
        result = responses[min(len(calls), len(responses)) - 1]
        if isinstance(result, Exception):
            raise result
        return httpx.Response(result)

    async def go():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        dispatcher = WebhookDispatcher((SUB,), client=client, max_attempts=3, sleep=no_sleep)
        ok = await dispatcher.deliver(SUB, EVENT)
        await client.aclose()
        return ok

    return asyncio.run(go()), calls


def test_delivery_is_signed_and_verifiable():
    ok, calls = run_delivery([200])
    assert ok and len(calls) == 1
    req = calls[0]
    verify([SUB.secret], req.content, req.headers["X-Bridge-Signature"])
    assert req.headers["X-Bridge-Event"] == "device.state"
    assert json.loads(req.content) == EVENT


def test_retries_on_5xx_and_network_errors_with_same_delivery_id():
    ok, calls = run_delivery([503, httpx.ConnectError("down"), 200])
    assert ok and len(calls) == 3
    assert len({c.headers["X-Bridge-Delivery"] for c in calls}) == 1


@pytest.mark.parametrize("status, attempts", [(400, 1), (410, 1), (429, 3), (500, 3)])
def test_retry_policy(status, attempts):
    ok, calls = run_delivery([status])
    assert not ok and len(calls) == attempts


def test_registry_events_reach_subscribers(make_client, webhook_log):
    sub = Subscriber("https://example.test/hook", "subscriber-secret-01", frozenset({"device.state"}))
    client = make_client(subscribers=(sub,))
    client.post("/devices/cct-1/commands", json={"brightness": 10}, headers={"X-API-Key": "write-key-0123456789"})
    deadline = time.time() + 2
    while time.time() < deadline and not any(b'"brightness":10' in r.content for r in webhook_log):
        time.sleep(0.02)
    hits = [r for r in webhook_log if b'"brightness":10' in r.content]
    assert hits, "state change was not delivered"
    assert all(json.loads(r.content)["type"] == "device.state" for r in webhook_log)
