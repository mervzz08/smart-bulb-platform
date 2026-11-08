#pragma once
// Flash persistence on LittleFS. Every write goes to a temp file and is then
// renamed over the original, so pulling power mid-write (which is exactly how
// people use light bulbs) never leaves a half-written file behind.

#include <Arduino.h>

#include "LightState.h"

struct NetConfig {
  String ssid;
  String wifiPass;
  String mqttHost;
  uint16_t mqttPort = 1883;
  String mqttUser;
  String mqttPass;
  String apPass;  // per-device setup/OTA password, generated on first boot

  bool hasWifi() const { return ssid.length() > 0; }
};

namespace storage {

bool begin();

bool loadConfig(NetConfig& cfg);
bool saveConfig(const NetConfig& cfg);

bool loadState(bulb::LightState& state, const bulb::Capabilities& caps);
bool saveState(const bulb::LightState& state, const bulb::Capabilities& caps);

// Power-cycle counter for the "switch it off and on 3 times" reset gesture.
uint8_t bumpBootCounter();
void clearBootCounter();

}  // namespace storage
