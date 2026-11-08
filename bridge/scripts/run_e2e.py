"""Start the whole stack as local processes, run e2e_smoke.py, tear down.

  python scripts/run_e2e.py                       # broker already on localhost:1883
  python scripts/run_e2e.py --broker-cmd amqtt    # also start a broker
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_DIR = HERE.parent
DEMO_ENV = BRIDGE_DIR.parent / "deploy" / "demo.env"
PY = sys.executable


def demo_env() -> dict[str, str]:
    env = dict(os.environ)
    for line in DEMO_ENV.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    env["MQTT_HOST"] = "127.0.0.1"
    env["BRIDGE_WEBHOOK_SUBSCRIBERS"] = env["BRIDGE_WEBHOOK_SUBSCRIBERS"].replace(
        "http://webhook-receiver:9000/", "http://127.0.0.1:9000/")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker-cmd", help="command that starts an MQTT broker on :1883")
    args = parser.parse_args()

    env = demo_env()
    logs = BRIDGE_DIR / ".e2e-logs"
    logs.mkdir(exist_ok=True)
    procs: dict[str, subprocess.Popen] = {}

    def start(name: str, cmd: list[str]) -> subprocess.Popen:
        out = open(logs / f"{name}.log", "w")
        procs[name] = subprocess.Popen(cmd, cwd=BRIDGE_DIR, env=env, stdout=out, stderr=subprocess.STDOUT)
        return procs[name]

    try:
        if args.broker_cmd:
            start("broker", shlex.split(args.broker_cmd))
            time.sleep(2)
        start("receiver", [PY, "-m", "simulators.webhook_receiver", "--port", "9000",
                           "--secret", "demo-subscriber-secret-0123"])
        start("bridge", [PY, "-m", "uvicorn", "--factory", "bulb_bridge.main:create_app",
                         "--host", "127.0.0.1", "--port", "8000"])
        start("sim-rgbcct", [PY, "-m", "simulators.bulb_sim", "--id", "sim-rgbcct", "--variant", "rgbcct",
                             "--host", "127.0.0.1"])
        rgbw = start("sim-rgbw", [PY, "-m", "simulators.bulb_sim", "--id", "sim-rgbw", "--variant", "rgbw",
                                  "--host", "127.0.0.1"])
        result = subprocess.run([PY, str(HERE / "e2e_smoke.py"), "--kill-pid", str(rgbw.pid)],
                                cwd=BRIDGE_DIR, env=env)
        if result.returncode != 0:
            for name in procs:
                print(f"\n----- {name}.log -----\n{(logs / f'{name}.log').read_text()[-3000:]}")
        return result.returncode
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for p in procs.values():
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    raise SystemExit(main())
