"""App factory. Run with:  uvicorn --factory bulb_bridge.main:create_app"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Callable, Protocol

import httpx
from fastapi import FastAPI

from . import __version__
from .auth import Authenticator
from .automations import AutomationEngine
from .config import Settings, load_settings
from .idempotency import IdempotencyStore
from .mqtt_bridge import MqttBridge
from .registry import Registry
from .routes import router
from .webhooks_out import WebhookDispatcher


class Bridge(Protocol):
    connected: bool

    def start(self, loop: asyncio.AbstractEventLoop) -> None: ...
    def stop(self) -> None: ...
    def publish_command(self, device_id: str, payload: dict[str, Any]) -> bool: ...


def create_app(
    settings: Settings | None = None,
    *,
    bridge_factory: Callable[[Settings, Registry], Bridge] = MqttBridge,
    webhook_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or load_settings()
    registry = Registry()
    bridge = bridge_factory(settings, registry)
    dispatcher = WebhookDispatcher(settings.subscribers, client=webhook_client)
    registry.add_listener(dispatcher.enqueue)
    engine = AutomationEngine(settings.rules, registry, bridge)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        bridge.start(loop)
        dispatch_task = loop.create_task(dispatcher.run())
        try:
            yield
        finally:
            dispatch_task.cancel()
            engine.cancel_all()
            bridge.stop()
            await dispatcher.aclose()

    app = FastAPI(title="Bulb Bridge", version=__version__, lifespan=lifespan,
                  description="REST, OAuth 2.0, signed webhooks and WebSockets for MQTT smart bulbs.")
    app.state.settings = settings
    app.state.registry = registry
    app.state.bridge = bridge
    app.state.auth = Authenticator(settings)
    app.state.engine = engine
    app.state.idempotency = IdempotencyStore()
    app.state.dispatcher = dispatcher
    app.include_router(router)
    return app
