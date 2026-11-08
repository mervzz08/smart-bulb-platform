import asyncio

from bulb_bridge.automations import AutomationEngine, state_to_command
from bulb_bridge.config import Rule
from bulb_bridge.models import LightCommand
from bulb_bridge.registry import Registry

GREEN = LightCommand(color={"r": 0, "g": 255, "b": 0}, effect="pulse")
WARM = {"state": "ON", "brightness": 120, "color_mode": "color_temp", "color_temp": 350, "effect": "none",
        "color": {"r": 255, "g": 255, "b": 255}}


class Recorder:
    def __init__(self):
        self.sent = []
        self.ok = True

    def publish_command(self, device_id, payload):
        self.sent.append((device_id, payload))
        return self.ok


def engine_with(duration):
    registry = Registry()
    registry.set_state("cct-1", WARM)
    pub = Recorder()
    rule = Rule("order.paid", "cct-1", GREEN, duration)
    return AutomationEngine((rule,), registry, pub, restore_transition_s=0.1), registry, pub


def test_state_to_command_recreates_each_mode():
    assert state_to_command(WARM, 0.5) == {"state": "ON", "brightness": 120, "color_temp": 350,
                                           "effect": "none", "transition": 0.5}
    rgb = {"state": "OFF", "brightness": 40, "color_mode": "rgb", "color": {"r": 1, "g": 2, "b": 3},
           "effect": "pulse"}
    assert state_to_command(rgb) == {"state": "OFF", "brightness": 40, "color": {"r": 1, "g": 2, "b": 3},
                                     "effect": "pulse", "transition": 0.5}
    white = {"state": "ON", "brightness": 90, "color_mode": "white", "effect": "none"}
    assert state_to_command(white)["white"] == 90


def test_flash_then_restore():
    async def go():
        engine, _, pub = engine_with(0.05)
        engine.handle("order.paid")
        assert pub.sent == [("cct-1", GREEN.to_payload())]
        await asyncio.sleep(0.1)
        assert pub.sent[-1] == ("cct-1", state_to_command(WARM, 0.1))

    asyncio.run(go())


def test_overlapping_alerts_restore_the_original_state():
    async def go():
        engine, registry, pub = engine_with(0.1)
        engine.handle("order.paid")
        # the bulb reports the green flash...
        registry.set_state("cct-1", {"state": "ON", "brightness": 255, "color_mode": "rgb",
                                     "color": {"r": 0, "g": 255, "b": 0}, "effect": "pulse"})
        await asyncio.sleep(0.05)
        engine.handle("order.paid")  # ...and a second order arrives mid-flash
        await asyncio.sleep(0.07)
        assert len(pub.sent) == 2  # window was extended, no early restore
        await asyncio.sleep(0.08)
        assert pub.sent[-1] == ("cct-1", state_to_command(WARM, 0.1))  # warm white, not green
        assert len(pub.sent) == 3

    asyncio.run(go())


def test_failed_send_does_not_schedule_restore():
    async def go():
        engine, _, pub = engine_with(0.02)
        pub.ok = False
        assert engine.handle("order.paid") == [{"device": "cct-1", "sent": False}]
        await asyncio.sleep(0.05)
        assert len(pub.sent) == 1

    asyncio.run(go())


def test_permanent_rule_has_no_restore():
    async def go():
        engine, _, pub = engine_with(None)
        engine.handle("order.paid")
        await asyncio.sleep(0.02)
        assert len(pub.sent) == 1

    asyncio.run(go())
