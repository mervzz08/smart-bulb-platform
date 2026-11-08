"""MQTT side of the bridge: listens to every bulb, publishes commands.

paho runs its network loop on its own thread. Incoming messages are handed to
the asyncio loop with call_soon_threadsafe, so the registry is only ever
touched from one thread.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import uuid
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .config import Settings
from .registry import Registry

log = logging.getLogger(__name__)

DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
CAPABILITY_KEYS = ("supported_color_modes", "min_mireds", "max_mireds", "effect_list")


def build_tls_context(ca_file: str | None) -> ssl.SSLContext:
    """TLS for MQTTS. With ca_file, trust only that CA (e.g. a private broker
    CA); without it, use the system trust store. Hostname checks stay on."""
    context = ssl.create_default_context(cafile=ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


class MqttBridge:
    def __init__(self, settings: Settings, registry: Registry) -> None:
        self._s = settings
        self._registry = registry
        self._loop: asyncio.AbstractEventLoop | None = None
        self.connected = False

        client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2,
                             client_id=f"bulb-bridge-{uuid.uuid4().hex[:8]}")
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        if settings.mqtt_tls:
            client.tls_set_context(build_tls_context(settings.mqtt_ca_file))
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client

    # ---- lifecycle -------------------------------------------------------
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        log.info("connecting to MQTT %s:%s (tls=%s)", self._s.mqtt_host, self._s.mqtt_port, self._s.mqtt_tls)
        self._client.connect_async(self._s.mqtt_host, self._s.mqtt_port, keepalive=30)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.disconnect()
        self._client.loop_stop()

    # ---- paho callbacks (network thread) --------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.warning("MQTT connect refused: %s", reason_code)
            return
        self.connected = True
        p, d = self._s.topic_prefix, self._s.discovery_prefix
        # Subscribing in on_connect means subscriptions come back after every reconnect.
        client.subscribe([(f"{p}/+/state", 1), (f"{p}/+/availability", 1),
                          (f"{p}/+/error", 0), (f"{d}/light/+/config", 1)])
        log.info("MQTT connected")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected = False
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.handle_message, msg.topic, msg.payload)

    # ---- message handling (event loop thread) ---------------------------
    def handle_message(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        try:
            if len(parts) == 4 and parts[0] == self._s.discovery_prefix and parts[1] == "light" \
                    and parts[3] == "config":
                self._on_discovery(parts[2], payload)
            elif len(parts) == 3 and parts[0] == self._s.topic_prefix:
                self._on_device_message(parts[1], parts[2], payload)
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("ignoring bad message on %s: %s", topic, e)

    def _on_discovery(self, device_id: str, payload: bytes) -> None:
        if not DEVICE_ID.match(device_id) or not payload:
            return  # empty retained payload = device removed from HA
        config = json.loads(payload)
        if not isinstance(config, dict):
            raise ValueError("discovery payload is not an object")
        caps: dict[str, Any] = {k: config[k] for k in CAPABILITY_KEYS if k in config}
        device = config.get("device") or {}
        caps["model"] = device.get("model")
        caps["sw_version"] = device.get("sw_version")
        self._registry.set_capabilities(device_id, caps)

    def _on_device_message(self, device_id: str, kind: str, payload: bytes) -> None:
        if not DEVICE_ID.match(device_id):
            return
        if kind == "state":
            state = json.loads(payload)
            if not isinstance(state, dict):
                raise ValueError("state payload is not an object")
            self._registry.set_state(device_id, state)
        elif kind == "availability":
            self._registry.set_availability(device_id, payload.decode() == "online")
        elif kind == "error":
            self._registry.record_error(device_id, payload.decode()[:200])

    # ---- outbound --------------------------------------------------------
    def publish_command(self, device_id: str, payload: dict[str, Any]) -> bool:
        if not self.connected:
            return False
        topic = f"{self._s.topic_prefix}/{device_id}/set"
        info = self._client.publish(topic, json.dumps(payload, separators=(",", ":")), qos=1)
        return info.rc == mqtt.MQTT_ERR_SUCCESS
