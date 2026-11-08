# Build log

Real problems hit while building this project, how each was diagnosed and what changed. Nothing here is invented. If something didn't happen, it isn't listed.

### 1. No C++ compiler on the development machine
**Symptom:** the firmware's host unit tests (`pio test -e native`) need gcc or clang. The Windows dev box had neither.
**Response:** this was planned for from the start. `lib/bulb_core` (colour mixing, command protocol, persistence format, discovery, backoff) has no Arduino includes, so it compiles on any host. The tests run in GitHub Actions on Ubuntu. The ESP8266 firmware itself is cross-compiled locally with PlatformIO's Xtensa toolchain.

### 2. `collectHeaders` stopped compiling after adding the local REST API
**Symptom:** `ESP8266WebServer-impl.h:663: error: conversion from 'const int' to 'String' is ambiguous` (plus three follow-on errors) when calling `collectHeaders(headerArray, 1)`.
**Diagnosis:** the error pointed inside the framework, not at my code, so I read the framework source. ESP8266 Arduino core 3.1 replaced the old `(const char* names[], size_t count)` overload with a variadic template that takes the names directly. The array and the `1` were being treated as two header names.
**Fix:** `api.collectHeaders("Authorization")`. Lesson: when a vendor library's docs and its installed version disagree, the installed headers win.

### 3. A security test that passed even when the code was broken
**Context:** I mutation-tested the bridge: broke four security-critical lines on purpose and checked that a test failed each time.
**Result:** three mutations were caught. The fourth, allowing `alg=none` JWTs, still passed every test.
**Diagnosis:** I probed PyJWT 2.15 directly. It raises `InvalidKeyError: When alg = "none", key value must be None` whenever a real key is supplied, so the library itself already blocks the classic `alg=none` attack.
**Outcome:** the test isn't weak. Pinning `algorithms=["HS256"]` is a second layer of defence, and it also protects against algorithm-confusion attacks if the code ever moves to asymmetric keys. Documented in `auth.py`.

### 4. Device API: unreadable auth check and invalid error JSON (caught in review)
**Symptom:** the first draft of the bulb's `/api/state` auth check was a nested condition that was correct but hard to verify by reading. The error response was built by string concatenation, which would produce invalid JSON for the one error message that contains quotes (the `color` format hint).
**Fix:** rewrote the check as a single condition, and built error bodies with ArduinoJson so quotes are escaped.

### 5. No Docker on the development machine
**Symptom:** the end-to-end test needs an MQTT broker, and Mosquitto normally runs in Docker.
**Response:** locally, the full stack (broker, bridge, two simulated bulbs, webhook receiver) ran as plain processes, with `amqtt`, a pure-Python broker, standing in for Mosquitto. CI runs the same `e2e_smoke.py` against real Mosquitto through `docker compose`, which also checks the Dockerfile and compose file.

### 6. Simulator: hack to re-announce after a Home Assistant restart (caught in review)
**Symptom:** the first draft re-used `on_connect` to re-announce after a Home Assistant restart by building a fake CONNACK reason code.
**Fix:** pulled the announce logic out into its own `announce()` function, called from both places.
