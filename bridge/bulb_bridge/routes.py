"""HTTP + WebSocket API."""

from __future__ import annotations

import asyncio
import base64
import binascii
from typing import Any
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .auth import Principal, require_scope
from .models import LightCommand, ShopEvent, unsupported_reason
from .registry import next_event
from .signing import SignatureError, verify

router = APIRouter()

MAX_WEBHOOK_BYTES = 64 * 1024
WS_AUTH_TIMEOUT_S = 5.0


# ---- health ------------------------------------------------------------------

@router.get("/health", tags=["meta"])
def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {"status": "ok", "mqtt_connected": state.bridge.connected, "devices": len(state.registry.all())}


# ---- OAuth 2.0 token endpoint (client credentials) ---------------------------

def _oauth_error(status: int, error: str, description: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="bulb-bridge"'
    return JSONResponse({"error": error, "error_description": description}, status_code=status, headers=headers)


def _basic_credentials(header: str) -> tuple[str, str] | None:
    scheme, _, encoded = header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip(), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return None
    client_id, sep, secret = decoded.partition(":")
    # RFC 6749 section 2.3.1: id and secret are form-urlencoded before Base64.
    return (unquote(client_id), unquote(secret)) if sep else None


@router.post("/oauth/token", tags=["auth"])
async def token(request: Request) -> JSONResponse:
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        return _oauth_error(400, "invalid_request", "use application/x-www-form-urlencoded")
    form = parse_qs((await request.body()).decode("utf-8", "replace"))

    def field(name: str) -> str | None:
        values = form.get(name)
        return values[0] if values else None

    if field("grant_type") != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type", "only client_credentials is supported")

    creds = _basic_credentials(request.headers.get("authorization", ""))
    if creds is None and field("client_id") and field("client_secret") is not None:
        creds = (field("client_id") or "", field("client_secret") or "")
    if creds is None:
        return _oauth_error(401, "invalid_client", "client authentication required")

    auth = request.app.state.auth
    client = auth.authenticate_client(*creds)
    if client is None:
        return _oauth_error(401, "invalid_client", "unknown client or wrong secret")

    requested = frozenset((field("scope") or "").split()) or client.scopes
    if not requested <= client.scopes:
        return _oauth_error(400, "invalid_scope", f"allowed scopes: {' '.join(sorted(client.scopes))}")

    return JSONResponse(auth.issue_token(client, requested),
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


# ---- devices -------------------------------------------------------------------

def _device_or_404(request: Request, device_id: str):
    device = request.app.state.registry.get(device_id)
    if device is None:
        raise HTTPException(404, f"unknown device {device_id!r}")
    return device


@router.get("/devices", tags=["devices"])
def list_devices(request: Request, _: Principal = Depends(require_scope("devices:read"))) -> list[dict]:
    return [d.to_dict() for d in request.app.state.registry.all()]


@router.get("/devices/{device_id}", tags=["devices"])
def get_device(device_id: str, request: Request,
               _: Principal = Depends(require_scope("devices:read"))) -> dict:
    return _device_or_404(request, device_id).to_dict()


@router.post("/devices/{device_id}/commands", tags=["devices"])
async def send_command(device_id: str, command: LightCommand, request: Request, wait: bool = True,
                       _: Principal = Depends(require_scope("devices:write"))) -> JSONResponse:
    """Send a command. With wait=true (default) the bridge waits briefly for
    the bulb to report its new state: 200 = confirmed, 202 = sent but not
    confirmed in time, 422 = rejected by the bulb, 503 = broker unreachable."""
    state = request.app.state
    device = _device_or_404(request, device_id)
    if not device.online:
        raise HTTPException(409, f"device {device_id!r} is offline")
    if reason := unsupported_reason(command, device.capabilities):
        raise HTTPException(422, reason)

    with state.registry.watch() as events:  # subscribe before publishing so the reply cannot be missed
        if not state.bridge.publish_command(device_id, command.to_payload()):
            raise HTTPException(503, "MQTT broker unavailable")
        if not wait:
            return JSONResponse({"status": "sent"}, status_code=202)
        event = await next_event(events, device_id, {"device.state", "device.error"},
                                 state.settings.command_wait_s)

    if event is None:
        return JSONResponse({"status": "sent", "confirmed": False}, status_code=202)
    if event["type"] == "device.error":
        raise HTTPException(422, f"device rejected command: {event['data']['error']}")
    return JSONResponse({"status": "confirmed", "state": event["data"]})


# ---- inbound e-commerce webhook --------------------------------------------------

@router.post("/webhooks/shop", tags=["webhooks"])
async def shop_webhook(request: Request) -> JSONResponse:
    state = request.app.state
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    body = await request.body()  # verify the exact raw bytes, before any JSON parsing
    if len(body) > MAX_WEBHOOK_BYTES:
        return JSONResponse({"error": "payload too large"}, status_code=413)

    try:
        verify(state.settings.shop_webhook_secrets, body, request.headers.get("x-shop-signature"),
               tolerance_s=state.settings.webhook_tolerance_s)
    except SignatureError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    try:
        event = ShopEvent.model_validate_json(body)
    except ValidationError:
        return JSONResponse({"error": "invalid event payload"}, status_code=400)

    if state.idempotency.seen(event.id):
        return JSONResponse({"status": "duplicate"})  # 2xx so the sender stops retrying

    if not state.engine.has_rules_for(event.type):
        state.idempotency.add(event.id)
        return JSONResponse({"status": "ignored"})

    actions = state.engine.handle(event.type)
    if not any(a["sent"] for a in actions):
        # Nothing reached the broker. Do not mark as processed: a 5xx makes
        # the shop retry later, when the broker may be back.
        return JSONResponse({"status": "retry_later", "actions": actions}, status_code=503)
    state.idempotency.add(event.id)
    return JSONResponse({"status": "processed", "actions": actions})


# ---- live events over WebSocket --------------------------------------------------

@router.websocket("/ws")
async def events_ws(websocket: WebSocket) -> None:
    """Protocol: connect, send {"type":"auth","token":"<bearer>"} (or
    "api_key") within 5 s, receive a snapshot, then one message per event.
    Auth is a first message, not a URL query parameter, so tokens do not end
    up in proxy and server access logs."""
    await websocket.accept()
    state = websocket.app.state
    try:
        hello = await asyncio.wait_for(websocket.receive_json(), WS_AUTH_TIMEOUT_S)
    except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
        await websocket.close(code=4401, reason="auth required")
        return

    principal = None
    if isinstance(hello, dict) and hello.get("type") == "auth":
        if hello.get("token"):
            principal = state.auth.principal_from_token(str(hello["token"]))
        elif hello.get("api_key"):
            principal = state.auth.principal_from_api_key(str(hello["api_key"]))
    if principal is None:
        await websocket.close(code=4401, reason="invalid credentials")
        return
    if "events:read" not in principal.scopes:
        await websocket.close(code=4403, reason="missing scope: events:read")
        return

    with state.registry.watch() as events:  # subscribe first, then snapshot: no gap
        await websocket.send_json({"type": "snapshot", "devices": [d.to_dict() for d in state.registry.all()]})

        async def pump() -> None:
            while True:
                await websocket.send_json(await events.get())

        async def drain() -> None:  # notices disconnects even when no events flow
            while True:
                await websocket.receive_text()

        tasks = {asyncio.create_task(pump()), asyncio.create_task(drain())}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
