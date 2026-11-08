#include "Storage.h"

#include <ArduinoJson.h>
#include <LittleFS.h>

#include "Command.h"

namespace {

constexpr const char* kConfigPath = "/config.json";
constexpr const char* kStatePath = "/state.json";
constexpr const char* kBootPath = "/boots";

bool atomicWrite(const char* path, const uint8_t* data, size_t len) {
  String tmp = String(path) + ".tmp";
  File f = LittleFS.open(tmp, "w");
  if (!f) return false;
  const bool ok = f.write(data, len) == len;
  f.close();
  if (!ok) {
    LittleFS.remove(tmp);
    return false;
  }
  return LittleFS.rename(tmp, path);
}

String readAll(const char* path) {
  File f = LittleFS.open(path, "r");
  if (!f) return String();
  String s = f.readString();
  f.close();
  return s;
}

}  // namespace

namespace storage {

bool begin() {
  if (LittleFS.begin()) return true;
  Serial.println(F("[fs] mount failed, formatting"));
  return LittleFS.format() && LittleFS.begin();
}

bool loadConfig(NetConfig& cfg) {
  const String raw = readAll(kConfigPath);
  if (raw.isEmpty()) return false;
  JsonDocument doc;
  if (deserializeJson(doc, raw)) return false;
  cfg.ssid = doc["ssid"] | "";
  cfg.wifiPass = doc["wifi_pass"] | "";
  cfg.mqttHost = doc["mqtt_host"] | "";
  cfg.mqttPort = doc["mqtt_port"] | 1883;
  cfg.mqttUser = doc["mqtt_user"] | "";
  cfg.mqttPass = doc["mqtt_pass"] | "";
  cfg.apPass = doc["ap_pass"] | "";
  return true;
}

bool saveConfig(const NetConfig& cfg) {
  JsonDocument doc;
  doc["ssid"] = cfg.ssid;
  doc["wifi_pass"] = cfg.wifiPass;
  doc["mqtt_host"] = cfg.mqttHost;
  doc["mqtt_port"] = cfg.mqttPort;
  doc["mqtt_user"] = cfg.mqttUser;
  doc["mqtt_pass"] = cfg.mqttPass;
  doc["ap_pass"] = cfg.apPass;
  String out;
  serializeJson(doc, out);
  return atomicWrite(kConfigPath, reinterpret_cast<const uint8_t*>(out.c_str()), out.length());
}

bool loadState(bulb::LightState& state, const bulb::Capabilities& caps) {
  const String raw = readAll(kStatePath);
  if (raw.isEmpty()) return false;
  return bulb::restoreState(state, raw.c_str(), raw.length(), caps);
}

bool saveState(const bulb::LightState& state, const bulb::Capabilities& caps) {
  char buf[256];
  const size_t n = bulb::writeState(state, caps, buf, sizeof buf);
  return n > 0 && atomicWrite(kStatePath, reinterpret_cast<const uint8_t*>(buf), n);
}

uint8_t bumpBootCounter() {
  uint8_t count = 0;
  File f = LittleFS.open(kBootPath, "r");
  if (f) {
    if (f.available()) count = f.read();
    f.close();
  }
  if (count < UINT8_MAX) ++count;
  atomicWrite(kBootPath, &count, 1);
  return count;
}

void clearBootCounter() { LittleFS.remove(kBootPath); }

}  // namespace storage
