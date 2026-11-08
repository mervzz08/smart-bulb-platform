# ESP8266 Smart Bulb Firmware

Firmware for Wi-Fi smart bulbs built on the ESP8266. One codebase builds two bulb variants:

| Variant | Channels | Colour modes |
|---|---|---|
| `rgbw` | Red, green, blue + cool white | RGB, white |
| `rgbcct` | Red, green, blue + cool white + warm white | RGB, tunable white (2700–6500 K) |

You control the bulb over **MQTT** using the Home Assistant JSON light schema. A bulb shows up in Home Assistant automatically, and any MQTT client works too.

> **Background.** This is an original implementation, written from scratch. It's inspired by smart-bulb firmware I worked on professionally in 2020, and contains no proprietary code, names or credentials.

## Features

- **One codebase, many bulbs.** Each variant is just a set of pin assignments in `platformio.ini`. The firmware works out what the hardware can do and only advertises colour modes it can actually show.
- **Works without the network.** The bulb restores its last state as soon as it gets power, before Wi-Fi starts, so the wall switch always works.
- **Setup through a captive portal.** On first boot the bulb opens a WPA2 access point called `Bulb-xxxxxx`. Your phone shows a setup page with a list of nearby networks. Each device gets its own random access-point password, created on first boot. No shared factory password exists.
- **Reset by power-cycling.** Switch the bulb off and on 3 times, each within 5 seconds of power-on, and it forgets its Wi-Fi and opens setup again. No button needed.
- **Home Assistant discovery.** The bulb publishes its config, a retained state and online/offline availability through MQTT's Last Will. If Home Assistant restarts, the bulb announces itself again.
- **Smooth light.** Gamma-corrected PWM at 2 kHz, timed fades (`transition`) and a `pulse` effect. Nothing in the main loop blocks.
- **Safe flash writes.** Each file is written to a temp file and then renamed over the old one, so cutting power mid-write can't corrupt the settings. Rapid changes are batched into one write to reduce flash wear.
- **Reliable reconnects.** MQTT reconnects with exponential backoff and jitter, so many bulbs don't all hit the broker at the same moment after an outage.
- **Strict commands.** A command is applied completely or not at all. An invalid field rejects the whole command, and the reason is published on `bulb/<id>/error`.
- **Password-protected OTA updates** through ArduinoOTA.
- **Tested.** Colour mixing, the command protocol, state persistence, discovery, backoff and password generation are plain C++ with no Arduino dependencies, and are unit-tested on the host in CI.

## Architecture

```
lib/bulb_core/        Plain C++, no Arduino headers, unit-tested on the host
  LightState.h        Hardware capabilities, light state, channel levels
  ColorMath.*         State -> channel mixing, gamma, fades, pulse curve
  Command.*           MQTT JSON protocol: validate/apply, serialize/restore
  Network.*           Topics, HA discovery, backoff, password generator

src/                  ESP8266-specific
  Profile.h           Variant pins -> Capabilities (from build flags)
  LedOutput.*         Non-blocking PWM driver with fades and effects
  Storage.*           LittleFS persistence (atomic writes, boot counter)
  Portal.*            Captive-portal setup (DNS + web server)
  MqttLink.*          MQTT session, LWT, reconnect backoff
  main.cpp            Wiring and the main loop

test/test_core/       Unity tests for lib/bulb_core
```

The split is deliberate. Everything that decides *what* the light should do lives in `bulb_core` and can be tested without hardware. `src/` only handles *how* to do it on an ESP8266.

## MQTT API

`<id>` is the last 6 hex digits of the chip ID (printed on the serial console at boot).

| Topic | Direction | Retained | Payload |
|---|---|---|---|
| `bulb/<id>/set` | in | — | Command JSON (below) |
| `bulb/<id>/state` | out | yes | Full state JSON |
| `bulb/<id>/availability` | out | yes | `online` / `offline` |
| `bulb/<id>/error` | out | no | Why the last command was rejected |
| `homeassistant/light/<id>/config` | out | yes | Discovery config |

**Command fields** (all optional; send only what you want to change):

| Field | Values | Notes |
|---|---|---|
| `state` | `"ON"`, `"OFF"`, `"TOGGLE"` | |
| `brightness` | 0–255 | `0` turns off but remembers the previous level |
| `color` | `{"r":0-255,"g":0-255,"b":0-255}` | Switches to RGB mode |
| `color_temp` | mireds, 153–370 | `rgbcct` only; clamped to the supported range |
| `white` | 0–255 | Switches to white mode at that brightness |
| `effect` | `"none"`, `"pulse"` | |
| `transition` | seconds (e.g. `1.5`) | Fade time; default 0.3 s, max 60 s |

Sending a colour, brightness or effect turns the light on unless you also send `"state":"OFF"`.

```sh
# warm orange, fading over 2 seconds
mosquitto_pub -t bulb/a1b2c3/set -m '{"color":{"r":255,"g":120,"b":20},"brightness":180,"transition":2}'

# warm white on the tunable bulb
mosquitto_pub -t bulb/a1b2c3/set -m '{"color_temp":370}'

# watch state and errors
mosquitto_sub -v -t 'bulb/a1b2c3/#'
```

## Build, test, flash

Requires [PlatformIO](https://platformio.org/) (`pip install platformio`).

```sh
pio test -e native             # unit tests (needs a host C++ compiler)
pio run                        # build both variants
pio run -e rgbcct -t upload    # flash over USB-serial
pio device monitor             # serial console, 115200 baud
```

After the first flash, later updates can go over Wi-Fi with `pio run -e rgbcct -t upload --upload-port bulb-<id>.local`. Use the device's setup password as the OTA password; it's printed on the serial console.

**Pins.** The defaults in `platformio.ini` (R=4, G=12, B=14, CW=5, WW=13) match a common ESP8266 bulb layout. Check your board and adjust before flashing.

**Flash size.** The target is a 1 MB module (ESP8285/ESP-01 class) with 64 KB of LittleFS. OTA needs room for two firmware images, so keep the build under roughly half of the flash left over for the sketch.

## First-time setup

1. Power the bulb. It pulses blue (or white) to show it's waiting.
2. Read the setup password from the serial console. A production unit would print it on a label.
3. Join the `Bulb-xxxxxx` network on your phone. The setup page opens automatically.
4. Enter your Wi-Fi network and MQTT broker details, then press **Save**. The bulb restarts and joins your network.

## Security notes

- Each device gets its own random setup/OTA password from the ESP8266 hardware RNG. Nothing secret is stored in the source code.
- Wi-Fi and MQTT credentials are stored in plain text in the device's flash. That's normal for this class of device, but anyone with physical access can read them.
- MQTT runs over plain TCP. On an untrusted network, use a broker on a separate IoT VLAN or add TLS. The ESP8266 can do TLS, but at a real cost in memory.

## License

MIT. See [LICENSE](LICENSE).
