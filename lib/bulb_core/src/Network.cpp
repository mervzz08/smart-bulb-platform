#include "Network.h"

#include <ArduinoJson.h>

namespace bulb {

Topics makeTopics(const std::string& deviceId, const std::string& prefix,
                  const std::string& discoveryPrefix) {
  const std::string base = prefix + "/" + deviceId;
  Topics t;
  t.set = base + "/set";
  t.state = base + "/state";
  t.availability = base + "/availability";
  t.error = base + "/error";
  t.discovery = discoveryPrefix + "/light/" + deviceId + "/config";
  t.haStatus = discoveryPrefix + "/status";
  return t;
}

size_t writeDiscovery(const DeviceInfo& dev, const Topics& t, const Capabilities& caps,
                      char* out, size_t outSize) {
  JsonDocument doc;
  doc["name"] = nullptr;  // use the device name as the entity name
  doc["unique_id"] = std::string("bulb_") + dev.id;
  doc["schema"] = "json";
  doc["command_topic"] = t.set;
  doc["state_topic"] = t.state;
  doc["availability_topic"] = t.availability;
  doc["brightness"] = true;

  JsonArray modes = doc["supported_color_modes"].to<JsonArray>();
  if (caps.rgb) modes.add("rgb");
  if (caps.tunableWhite()) {
    modes.add("color_temp");
    doc["min_mireds"] = caps.minMireds;
    doc["max_mireds"] = caps.maxMireds;
  } else if (caps.anyWhite()) {
    modes.add("white");
  }

  doc["effect"] = true;
  JsonArray effects = doc["effect_list"].to<JsonArray>();
  effects.add("none");
  effects.add("pulse");

  JsonObject device = doc["device"].to<JsonObject>();
  device["identifiers"].to<JsonArray>().add(std::string("bulb_") + dev.id);
  device["name"] = dev.name;
  device["model"] = dev.model;
  device["sw_version"] = dev.swVersion;

  if (measureJson(doc) >= outSize) return 0;
  return serializeJson(doc, out, outSize);
}

uint32_t backoffDelayMs(uint8_t attempt, float jitter01, uint32_t baseMs, uint32_t capMs) {
  uint64_t delay = baseMs;
  for (uint8_t i = 0; i < attempt && delay < capMs; ++i) delay *= 2;
  if (delay > capMs) delay = capMs;
  if (jitter01 < 0.0f) jitter01 = 0.0f;
  if (jitter01 >= 1.0f) jitter01 = 0.999f;
  const double scaled = static_cast<double>(delay) * (0.75 + 0.5 * jitter01);
  return scaled > capMs ? capMs : static_cast<uint32_t>(scaled);
}

std::string makePassword(size_t length, uint32_t (*rng)()) {
  static const char kAlphabet[] = "abcdefghjkmnpqrstuvwxyz23456789";
  constexpr uint32_t kSize = sizeof(kAlphabet) - 1;
  // Largest multiple of kSize that fits in 32 bits; values at or above it
  // would make the first few characters slightly more likely, so redraw.
  constexpr uint32_t kLimit = UINT32_MAX - (UINT32_MAX % kSize);

  std::string out;
  out.reserve(length);
  while (out.size() < length) {
    const uint32_t r = rng();
    if (r >= kLimit) continue;
    out.push_back(kAlphabet[r % kSize]);
  }
  return out;
}

}  // namespace bulb
