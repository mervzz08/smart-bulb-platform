"""Simulated bulb on MQTT, protocol-identical to the firmware.

  python -m simulators.bulb_sim --id sim-rgbcct --variant rgbcct --host localhost
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import threading

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .bulb_model import VARIANTS, CommandError, apply_command, default_state, discovery, to_wire

log = logging.getLogger("bulb_sim")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True)
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="rgbcct")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s [{args.id}] %(message)s")

    caps = VARIANTS[args.variant]
    model = args.variant.upper()
    base = f"bulb/{args.id}"
    topics = {"set": f"{base}/set", "state": f"{base}/state",
              "availability": f"{base}/availability", "error": f"{base}/error"}
    state = default_state(caps)

    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=f"bulb-{args.id}")
    client.will_set(topics["availability"], "offline", qos=1, retain=True)  # broker says "offline" if we vanish
    client.reconnect_delay_set(1, 60)

    def publish_state() -> None:
        client.publish(topics["state"], json.dumps(to_wire(state, caps)), qos=1, retain=True)

    def announce() -> None:
        client.publish(topics["availability"], "online", qos=1, retain=True)
        client.publish(f"homeassistant/light/{args.id}/config",
                       json.dumps(discovery(args.id, model, caps, topics)), qos=1, retain=True)
        publish_state()

    def on_connect(c, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.warning("connect refused: %s", reason_code)
            return
        c.subscribe(topics["set"], qos=1)
        c.subscribe("homeassistant/status")
        announce()
        log.info("online as %s (%s)", args.id, model)

    def on_message(c, userdata, msg):
        nonlocal state
        if msg.topic == "homeassistant/status":
            if msg.payload == b"online":
                announce()  # Home Assistant restarted and forgot us
            return
        try:
            state, transition_ms = apply_command(state, json.loads(msg.payload), caps)
        except (CommandError, json.JSONDecodeError) as e:
            reason = str(e) if isinstance(e, CommandError) else "invalid JSON"
            log.info("rejected: %s", reason)
            c.publish(topics["error"], reason, qos=0)
            return
        log.info("applied %s (fade %d ms)", msg.payload.decode(), transition_ms)
        publish_state()

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(args.host, args.port, keepalive=30)
    client.loop_start()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    stop.wait()
    # Graceful shutdown: say goodbye explicitly (the LWT only fires on an unclean drop).
    client.publish(topics["availability"], "offline", qos=1, retain=True).wait_for_publish(2)
    client.disconnect()
    client.loop_stop()


if __name__ == "__main__":
    main()
