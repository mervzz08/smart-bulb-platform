"""Request/response models. LightCommand mirrors the firmware's MQTT protocol
(Home Assistant JSON light schema), so bad commands are rejected at the API
edge with a clear 422 instead of travelling to the bulb and failing there."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Color(BaseModel):
    model_config = ConfigDict(extra="forbid")
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)


class LightCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ON", "OFF", "TOGGLE"] | None = None
    brightness: int | None = Field(None, ge=0, le=255)
    color: Color | None = None
    color_temp: int | None = Field(None, ge=1, le=1000, description="mireds")
    white: int | None = Field(None, ge=0, le=255)
    effect: Literal["none", "pulse"] | None = None
    transition: float | None = Field(None, ge=0, le=60, description="seconds")

    @model_validator(mode="after")
    def _check(self) -> "LightCommand":
        if self.color is not None and self.color_temp is not None:
            raise ValueError("send either color or color_temp, not both")
        if not self.model_fields_set:
            raise ValueError("command is empty")
        return self

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def unsupported_reason(cmd: LightCommand, capabilities: dict[str, Any]) -> str | None:
    """Check a command against what the device advertised in discovery.
    Returns None if it is fine (or capabilities are not known yet)."""
    modes = capabilities.get("supported_color_modes")
    if not modes:
        return None
    if cmd.color is not None and "rgb" not in modes:
        return "device does not support rgb color"
    if cmd.color_temp is not None and "color_temp" not in modes:
        return "device does not support color_temp"
    if cmd.white is not None and not ({"white", "color_temp"} & set(modes)):
        return "device has no white channel"
    effects = capabilities.get("effect_list")
    if cmd.effect is not None and effects and cmd.effect not in effects:
        return f"device does not support effect {cmd.effect!r}"
    return None


class ShopEvent(BaseModel):
    """Inbound e-commerce event, e.g. {"id": "evt_123", "type": "order.paid", ...}."""

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    created_at: int | None = None
    data: dict[str, Any] = Field(default_factory=dict)
