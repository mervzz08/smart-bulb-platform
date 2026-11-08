#include "Command.h"

#include <ArduinoJson.h>

#include <cmath>
#include <cstring>

namespace bulb {

namespace {

CommandResult fail(const char* why) { return {false, 0, why}; }

bool readNumber(JsonVariantConst v, float& out) {
  if (!v.is<float>()) return false;  // true for integers as well
  out = v.as<float>();
  return std::isfinite(out);
}

long clampLong(long v, long lo, long hi) { return v < lo ? lo : (v > hi ? hi : v); }

bool readByte(JsonVariantConst v, uint8_t& out) {
  float f;
  if (!readNumber(v, f)) return false;
  out = static_cast<uint8_t>(clampLong(std::lround(f), 0, 255));
  return true;
}

const char* modeName(ColorMode m) {
  switch (m) {
    case ColorMode::Rgb: return "rgb";
    case ColorMode::ColorTemp: return "color_temp";
    default: return "white";
  }
}

bool parseEffect(const char* name, Effect& out) {
  if (!name) return false;
  if (std::strcmp(name, "none") == 0) { out = Effect::None; return true; }
  if (std::strcmp(name, "pulse") == 0) { out = Effect::Pulse; return true; }
  return false;
}

uint16_t clampMireds(float m, const Capabilities& caps) {
  return static_cast<uint16_t>(clampLong(std::lround(m), caps.minMireds, caps.maxMireds));
}

}  // namespace

CommandResult applyCommand(LightState& state, const char* json, size_t len,
                           const Capabilities& caps) {
  JsonDocument doc;
  if (deserializeJson(doc, json, len)) return fail("invalid JSON");
  if (!doc.is<JsonObjectConst>()) return fail("payload must be a JSON object");

  LightState next = state;
  bool impliesOn = false;  // setting a color or brightness turns the light on
  bool forceOff = false;
  int requestedPower = -1;  // -1 = not specified, 0 = off, 1 = on
  uint32_t transitionMs = kDefaultTransitionMs;

  if (!doc["state"].isNull()) {
    const char* s = doc["state"].as<const char*>();
    if (s && std::strcmp(s, "ON") == 0) requestedPower = 1;
    else if (s && std::strcmp(s, "OFF") == 0) requestedPower = 0;
    else if (s && std::strcmp(s, "TOGGLE") == 0) requestedPower = state.on ? 0 : 1;
    else return fail("state must be ON, OFF or TOGGLE");
  }

  const bool hasColor = !doc["color"].isNull();
  const bool hasTemp = !doc["color_temp"].isNull();
  if (hasColor && hasTemp) return fail("send either color or color_temp, not both");

  if (hasColor) {
    if (!caps.rgb) return fail("this bulb has no RGB channels");
    JsonVariantConst c = doc["color"];
    if (!c.is<JsonObjectConst>() || !readByte(c["r"], next.r) ||
        !readByte(c["g"], next.g) || !readByte(c["b"], next.b)) {
      return fail("color must be {\"r\":0-255,\"g\":0-255,\"b\":0-255}");
    }
    next.mode = ColorMode::Rgb;
    impliesOn = true;
  }

  if (hasTemp) {
    if (!caps.tunableWhite()) return fail("this bulb has no tunable white");
    float m;
    if (!readNumber(doc["color_temp"], m)) return fail("color_temp must be a number (mireds)");
    next.mireds = clampMireds(m, caps);
    next.mode = ColorMode::ColorTemp;
    impliesOn = true;
  }

  if (!doc["white"].isNull()) {
    if (!caps.anyWhite()) return fail("this bulb has no white channel");
    uint8_t w;
    if (!readByte(doc["white"], w)) return fail("white must be a number 0-255");
    next.mode = caps.tunableWhite() ? ColorMode::ColorTemp : ColorMode::White;
    if (w == 0) forceOff = true; else next.brightness = w;
    impliesOn = true;
  }

  if (!doc["brightness"].isNull()) {
    uint8_t b;
    if (!readByte(doc["brightness"], b)) return fail("brightness must be a number 0-255");
    // Brightness 0 means "off" but keeps the previous level for the next ON.
    if (b == 0) forceOff = true; else next.brightness = b;
    impliesOn = true;
  }

  if (!doc["effect"].isNull()) {
    if (!parseEffect(doc["effect"].as<const char*>(), next.effect)) {
      return fail("effect must be none or pulse");
    }
    impliesOn = true;
  }

  if (!doc["transition"].isNull()) {
    float seconds;
    if (!readNumber(doc["transition"], seconds) || seconds < 0) {
      return fail("transition must be a non-negative number of seconds");
    }
    const float ms = seconds * 1000.0f;
    transitionMs = ms > kMaxTransitionMs ? kMaxTransitionMs : static_cast<uint32_t>(std::lround(ms));
  }

  if (forceOff) next.on = false;
  else if (requestedPower >= 0) next.on = requestedPower == 1;
  else if (impliesOn) next.on = true;

  state = next;
  return {true, transitionMs, nullptr};
}

size_t writeState(const LightState& s, const Capabilities& caps, char* out, size_t outSize) {
  JsonDocument doc;
  doc["state"] = s.on ? "ON" : "OFF";
  doc["brightness"] = s.brightness;
  doc["color_mode"] = modeName(s.mode);
  if (caps.rgb) {
    JsonObject c = doc["color"].to<JsonObject>();
    c["r"] = s.r;
    c["g"] = s.g;
    c["b"] = s.b;
  }
  if (caps.tunableWhite()) doc["color_temp"] = s.mireds;
  doc["effect"] = s.effect == Effect::Pulse ? "pulse" : "none";

  if (measureJson(doc) >= outSize) return 0;  // leave room for the terminator
  return serializeJson(doc, out, outSize);
}

bool restoreState(LightState& s, const char* json, size_t len, const Capabilities& caps) {
  JsonDocument doc;
  if (deserializeJson(doc, json, len) || !doc.is<JsonObjectConst>()) return false;

  const char* power = doc["state"].as<const char*>();
  if (power) s.on = std::strcmp(power, "OFF") != 0;

  uint8_t b;
  if (readByte(doc["brightness"], b) && b > 0) s.brightness = b;

  JsonVariantConst c = doc["color"];
  if (caps.rgb && c.is<JsonObjectConst>()) {
    readByte(c["r"], s.r);
    readByte(c["g"], s.g);
    readByte(c["b"], s.b);
  }

  float m;
  if (caps.tunableWhite() && readNumber(doc["color_temp"], m)) s.mireds = clampMireds(m, caps);

  parseEffect(doc["effect"].as<const char*>(), s.effect);

  const char* mode = doc["color_mode"].as<const char*>();
  if (mode) {
    if (std::strcmp(mode, "rgb") == 0 && caps.rgb) s.mode = ColorMode::Rgb;
    else if (std::strcmp(mode, "color_temp") == 0 && caps.tunableWhite()) s.mode = ColorMode::ColorTemp;
    else if (std::strcmp(mode, "white") == 0 && caps.anyWhite()) s.mode = ColorMode::White;
  }
  return true;
}

}  // namespace bulb
