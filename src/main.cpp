// ESP8266 smart bulb firmware: RGB + white (fixed or tunable), MQTT control
// with Home Assistant discovery, captive-portal setup, OTA updates.

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>

#include "ColorMath.h"
#include "Command.h"
#include "LedOutput.h"
#include "MqttLink.h"
#include "Network.h"
#include "Portal.h"
#include "Profile.h"
#include "Storage.h"

#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION "dev"
#endif

namespace {

constexpr uint8_t kResetPowerCycles = 3;   // off/on this many times quickly to re-run setup
constexpr uint32_t kBootWindowMs = 5000;   // ...each within this long of power-on
constexpr uint32_t kSaveDebounceMs = 2000; // batch rapid changes into one flash write
constexpr size_t kApPasswordLength = 10;

const bulb::Capabilities kCaps = profile::capabilities();

bulb::LightState state;
LedOutput led;
NetConfig config;
Portal portal;
MqttLink mqtt;
bulb::Topics topics;
String deviceId;

bool provisioning = false;
bool bootWindowOpen = true;
bool otaStarted = false;
bool stateDirty = false;
uint32_t stateDirtyAtMs = 0;

uint32_t hardwareRandom() { return ESP.random(); }

void showState(uint32_t transitionMs) {
  led.setTarget(bulb::computeChannels(state, kCaps), transitionMs);
  led.setEffect(state.effect);
}

void publishState() {
  char buf[256];
  if (bulb::writeState(state, kCaps, buf, sizeof buf)) mqtt.publishState(buf);
}

void announce() {
  const String name = String(profile::kModel) + " " + deviceId;
  const bulb::DeviceInfo info{deviceId.c_str(), name.c_str(), profile::kModel, FIRMWARE_VERSION};
  char buf[900];
  if (bulb::writeDiscovery(info, topics, kCaps, buf, sizeof buf)) mqtt.publishDiscovery(buf);
  publishState();
}

void onCommand(const char* payload, size_t len) {
  const bulb::CommandResult r = bulb::applyCommand(state, payload, len, kCaps);
  if (!r.ok) {
    Serial.printf("[cmd] rejected: %s\n", r.error);
    mqtt.publishError(r.error);
    return;
  }
  showState(r.transitionMs);
  publishState();
  stateDirty = true;
  stateDirtyAtMs = millis();
}

void startProvisioning() {
  provisioning = true;
  const String ssid = "Bulb-" + deviceId;
  // Printed on the serial console; a production unit would carry it on a label.
  Serial.printf("[setup] join \"%s\" with password \"%s\"\n", ssid.c_str(), config.apPass.c_str());

  bulb::Channels hint;  // slow pulse so the user can see the bulb is waiting
  if (kCaps.rgb) hint.b = 0.6f;
  else hint.cw = hint.ww = 0.6f;
  led.setTarget(hint, 500);
  led.setEffect(bulb::Effect::Pulse);

  portal.begin(ssid, config);
}

void startNetwork() {
  const String host = "bulb-" + deviceId;
  WiFi.persistent(false);  // we store credentials ourselves; avoid SDK flash writes
  WiFi.mode(WIFI_STA);
  WiFi.hostname(host);
  WiFi.setAutoReconnect(true);
  WiFi.begin(config.ssid.c_str(), config.wifiPass.c_str());
  Serial.printf("[wifi] connecting to \"%s\"\n", config.ssid.c_str());

  mqtt.begin(config, topics, host, onCommand, announce);
}

void startOta() {
  ArduinoOTA.setHostname(("bulb-" + deviceId).c_str());
  ArduinoOTA.setPassword(config.apPass.c_str());
  ArduinoOTA.onStart([] { Serial.println(F("[ota] update started")); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[ota] error %u\n", e); });
  ArduinoOTA.begin();
  otaStarted = true;
  Serial.printf("[wifi] connected, ip %s\n", WiFi.localIP().toString().c_str());
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.println();

  char id[7];
  snprintf(id, sizeof id, "%06x", ESP.getChipId());
  deviceId = id;
  Serial.printf("[boot] %s %s, firmware %s\n", profile::kModel, id, FIRMWARE_VERSION);

  storage::begin();

  // Light comes on at its last state immediately, before any networking:
  // a bulb must work from the wall switch even when Wi-Fi is down.
  state = bulb::defaultState(kCaps);
  storage::loadState(state, kCaps);
  led.begin();
  showState(0);

  storage::loadConfig(config);
  if (storage::bumpBootCounter() >= kResetPowerCycles) {
    Serial.println(F("[boot] power-cycle reset: forgetting Wi-Fi"));
    storage::clearBootCounter();
    bootWindowOpen = false;
    config.ssid = "";
    config.wifiPass = "";
    storage::saveConfig(config);
  }
  if (config.apPass.length() < 8) {
    config.apPass = bulb::makePassword(kApPasswordLength, hardwareRandom).c_str();
    storage::saveConfig(config);
  }

  topics = bulb::makeTopics(deviceId.c_str());

  if (config.hasWifi()) startNetwork();
  else startProvisioning();
}

void loop() {
  const uint32_t now = millis();
  led.update(now);

  if (bootWindowOpen && now > kBootWindowMs) {
    storage::clearBootCounter();
    bootWindowOpen = false;
  }

  if (provisioning) {
    if (portal.loop()) {
      storage::saveConfig(portal.result());
      Serial.println(F("[setup] saved, restarting"));
      delay(800);  // let the HTTP response reach the phone
      ESP.restart();
    }
    return;
  }

  if (!otaStarted && WiFi.status() == WL_CONNECTED) startOta();
  if (otaStarted) ArduinoOTA.handle();
  mqtt.loop(now);

  if (stateDirty && now - stateDirtyAtMs >= kSaveDebounceMs) {
    storage::saveState(state, kCaps);
    stateDirty = false;
  }
}
