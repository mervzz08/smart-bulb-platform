"""Settings from environment variables. Everything is validated at startup so
a typo in a scope or rule fails loudly at boot, not silently at 2 a.m."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from pydantic import ValidationError

from .models import LightCommand

ALL_SCOPES = frozenset({"devices:read", "devices:write", "events:read"})
EVENT_TYPES = frozenset({"device.state", "device.availability", "device.error", "device.discovered"})


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    secret_sha256: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class ApiKey:
    name: str
    scopes: frozenset[str]


@dataclass(frozen=True)
class Subscriber:
    url: str
    secret: str
    events: frozenset[str]

    def wants(self, event_type: str) -> bool:
        return "*" in self.events or event_type in self.events


@dataclass(frozen=True)
class Rule:
    event: str
    device: str
    command: LightCommand
    duration_s: float | None = None


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    shop_webhook_secrets: tuple[str, ...]
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_ca_file: str | None = None
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    topic_prefix: str = "bulb"
    discovery_prefix: str = "homeassistant"
    token_ttl_s: int = 900
    webhook_tolerance_s: int = 300
    command_wait_s: float = 3.0
    api_keys: Mapping[str, ApiKey] = field(default_factory=dict)  # sha256(key) -> ApiKey
    oauth_clients: Mapping[str, OAuthClient] = field(default_factory=dict)
    subscribers: tuple[Subscriber, ...] = ()
    rules: tuple[Rule, ...] = ()


def _json(env: Mapping[str, str], name: str) -> list[Any]:
    raw = env.get(name, "").strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{name} is not valid JSON: {e}") from e
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a JSON array")
    return value


def _scopes(values: Any, where: str) -> frozenset[str]:
    scopes = frozenset(values or [])
    unknown = scopes - ALL_SCOPES
    if unknown:
        raise ConfigError(f"{where}: unknown scopes {sorted(unknown)}")
    return scopes


def _sha256_hex(value: Any, where: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ConfigError(f"{where}: expected a lowercase sha256 hex digest")
    return value


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    jwt_secret = env.get("BRIDGE_JWT_SECRET", "")
    if len(jwt_secret) < 32:
        raise ConfigError("BRIDGE_JWT_SECRET must be set and at least 32 characters")

    shop_secrets = tuple(s.strip() for s in env.get("BRIDGE_SHOP_WEBHOOK_SECRETS", "").split(",") if s.strip())
    if not shop_secrets or any(len(s) < 16 for s in shop_secrets):
        raise ConfigError("BRIDGE_SHOP_WEBHOOK_SECRETS must list one or more secrets of 16+ characters")

    api_keys: dict[str, ApiKey] = {}
    for i, item in enumerate(_json(env, "BRIDGE_API_KEYS")):
        where = f"BRIDGE_API_KEYS[{i}]"
        api_keys[_sha256_hex(item.get("sha256"), where)] = ApiKey(
            name=str(item.get("name") or f"key-{i}"), scopes=_scopes(item.get("scopes"), where)
        )

    clients: dict[str, OAuthClient] = {}
    for i, item in enumerate(_json(env, "BRIDGE_OAUTH_CLIENTS")):
        where = f"BRIDGE_OAUTH_CLIENTS[{i}]"
        cid = item.get("client_id")
        if not cid:
            raise ConfigError(f"{where}: client_id is required")
        clients[cid] = OAuthClient(cid, _sha256_hex(item.get("secret_sha256"), where), _scopes(item.get("scopes"), where))

    subscribers = []
    for i, item in enumerate(_json(env, "BRIDGE_WEBHOOK_SUBSCRIBERS")):
        where = f"BRIDGE_WEBHOOK_SUBSCRIBERS[{i}]"
        url, secret = item.get("url", ""), item.get("secret", "")
        if not url.startswith(("http://", "https://")):
            raise ConfigError(f"{where}: url must be http(s)")
        if len(secret) < 16:
            raise ConfigError(f"{where}: secret must be 16+ characters")
        events = frozenset(item.get("events") or ["*"])
        if events - EVENT_TYPES - {"*"}:
            raise ConfigError(f"{where}: unknown events {sorted(events - EVENT_TYPES - {'*'})}")
        subscribers.append(Subscriber(url, secret, events))

    rules = []
    for i, item in enumerate(_json(env, "BRIDGE_RULES")):
        where = f"BRIDGE_RULES[{i}]"
        try:
            command = LightCommand.model_validate(item.get("command"))
        except ValidationError as e:
            raise ConfigError(f"{where}: invalid command: {e}") from e
        if not item.get("event") or not item.get("device"):
            raise ConfigError(f"{where}: event and device are required")
        duration = item.get("duration_s")
        rules.append(Rule(item["event"], item["device"], command, float(duration) if duration else None))

    tls = _bool(env.get("MQTT_TLS"))
    return Settings(
        jwt_secret=jwt_secret,
        shop_webhook_secrets=shop_secrets,
        mqtt_host=env.get("MQTT_HOST", "localhost"),
        mqtt_port=int(env.get("MQTT_PORT") or (8883 if tls else 1883)),
        mqtt_tls=tls,
        mqtt_ca_file=env.get("MQTT_CA_FILE") or None,
        mqtt_username=env.get("MQTT_USERNAME") or None,
        mqtt_password=env.get("MQTT_PASSWORD") or None,
        token_ttl_s=int(env.get("BRIDGE_TOKEN_TTL_S", "900")),
        webhook_tolerance_s=int(env.get("BRIDGE_WEBHOOK_TOLERANCE_S", "300")),
        command_wait_s=float(env.get("BRIDGE_COMMAND_WAIT_S", "3")),
        api_keys=api_keys,
        oauth_clients=clients,
        subscribers=tuple(subscribers),
        rules=tuple(rules),
    )
