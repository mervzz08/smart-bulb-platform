"""Python port of the firmware's command rules (lib/bulb_core/src/Command.cpp),
used by the simulated bulb. Same validation, same all-or-nothing behaviour,
same error strings, so the bridge cannot tell a simulator from a real bulb."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

DEFAULT_TRANSITION_MS = 300
MAX_TRANSITION_MS = 60000


@dataclass(frozen=True)
class Caps:
    rgb: bool = True
    cool_white: bool = True
    warm_white: bool = False
    min_mireds: int = 153
    max_mireds: int = 370

    @property
    def tunable(self) -> bool:
        return self.cool_white and self.warm_white

    @property
    def any_white(self) -> bool:
        return self.cool_white or self.warm_white


VARIANTS = {"rgbw": Caps(), "rgbcct": Caps(warm_white=True)}


class CommandError(ValueError):
    pass


def default_state(caps: Caps) -> dict[str, Any]:
    mode = "color_temp" if caps.tunable else ("white" if caps.any_white else "rgb")
    return {"on": True, "brightness": 255, "mode": mode, "rgb": [255, 255, 255],
            "mireds": (caps.min_mireds + caps.max_mireds) // 2, "effect": "none"}


def _number(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandError(what)
    return float(value)


def _byte(value: Any, what: str) -> int:
    return max(0, min(255, round(_number(value, what))))


def apply_command(state: dict[str, Any], cmd: Any, caps: Caps) -> tuple[dict[str, Any], int]:
    """Return (new_state, transition_ms) or raise CommandError. Never mutates state."""
    if not isinstance(cmd, dict):
        raise CommandError("payload must be a JSON object")
    nxt = copy.deepcopy(state)
    implies_on = force_off = False
    power: bool | None = None
    transition_ms = DEFAULT_TRANSITION_MS

    if "state" in cmd:
        power = {"ON": True, "OFF": False, "TOGGLE": not state["on"]}.get(cmd["state"])
        if power is None:
            raise CommandError("state must be ON, OFF or TOGGLE")
    if "color" in cmd and "color_temp" in cmd:
        raise CommandError("send either color or color_temp, not both")
    if "color" in cmd:
        if not caps.rgb:
            raise CommandError("this bulb has no RGB channels")
        c = cmd["color"]
        msg = 'color must be {"r":0-255,"g":0-255,"b":0-255}'
        if not isinstance(c, dict) or not {"r", "g", "b"} <= c.keys():
            raise CommandError(msg)
        nxt["rgb"] = [_byte(c[k], msg) for k in ("r", "g", "b")]
        nxt["mode"], implies_on = "rgb", True
    if "color_temp" in cmd:
        if not caps.tunable:
            raise CommandError("this bulb has no tunable white")
        m = _number(cmd["color_temp"], "color_temp must be a number (mireds)")
        nxt["mireds"] = max(caps.min_mireds, min(caps.max_mireds, round(m)))
        nxt["mode"], implies_on = "color_temp", True
    if "white" in cmd:
        if not caps.any_white:
            raise CommandError("this bulb has no white channel")
        w = _byte(cmd["white"], "white must be a number 0-255")
        nxt["mode"] = "color_temp" if caps.tunable else "white"
        if w == 0:
            force_off = True
        else:
            nxt["brightness"] = w
        implies_on = True
    if "brightness" in cmd:
        b = _byte(cmd["brightness"], "brightness must be a number 0-255")
        if b == 0:
            force_off = True
        else:
            nxt["brightness"] = b
        implies_on = True
    if "effect" in cmd:
        if cmd["effect"] not in ("none", "pulse"):
            raise CommandError("effect must be none or pulse")
        nxt["effect"], implies_on = cmd["effect"], True
    if "transition" in cmd:
        s = _number(cmd["transition"], "transition must be a non-negative number of seconds")
        if s < 0:
            raise CommandError("transition must be a non-negative number of seconds")
        transition_ms = min(MAX_TRANSITION_MS, round(s * 1000))

    if force_off:
        nxt["on"] = False
    elif power is not None:
        nxt["on"] = power
    elif implies_on:
        nxt["on"] = True
    return nxt, transition_ms


def to_wire(state: dict[str, Any], caps: Caps) -> dict[str, Any]:
    """Same JSON the firmware publishes on bulb/<id>/state."""
    out: dict[str, Any] = {"state": "ON" if state["on"] else "OFF", "brightness": state["brightness"],
                           "color_mode": state["mode"]}
    if caps.rgb:
        out["color"] = dict(zip("rgb", state["rgb"]))
    if caps.tunable:
        out["color_temp"] = state["mireds"]
    out["effect"] = state["effect"]
    return out


def discovery(device_id: str, model: str, caps: Caps, topics: dict[str, str]) -> dict[str, Any]:
    modes = (["rgb"] if caps.rgb else []) + (["color_temp"] if caps.tunable else ["white"] if caps.any_white else [])
    cfg: dict[str, Any] = {
        "name": None, "unique_id": f"bulb_{device_id}", "schema": "json",
        "command_topic": topics["set"], "state_topic": topics["state"],
        "availability_topic": topics["availability"], "brightness": True,
        "supported_color_modes": modes, "effect": True, "effect_list": ["none", "pulse"],
        "device": {"identifiers": [f"bulb_{device_id}"], "name": f"{model} {device_id}",
                   "model": model, "sw_version": "sim-1.0.0"},
    }
    if caps.tunable:
        cfg["min_mireds"], cfg["max_mireds"] = caps.min_mireds, caps.max_mireds
    return cfg
