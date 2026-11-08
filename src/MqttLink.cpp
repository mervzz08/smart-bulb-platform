#include "MqttLink.h"

#include <cstring>

namespace {
constexpr uint16_t kBufferSize = 1024;   // discovery payload is ~600 bytes
constexpr uint16_t kSocketTimeoutS = 4;  // bound how long a dead broker can stall the loop
}  // namespace

void MqttLink::begin(const NetConfig& cfg, const bulb::Topics& topics, const String& clientId,
                     CommandHandler onCommand, ConnectedHandler onConnected) {
  cfg_ = cfg;
  topics_ = topics;
  clientId_ = clientId;
  onCommand_ = std::move(onCommand);
  onConnected_ = std::move(onConnected);

  client_.setServer(cfg_.mqttHost.c_str(), cfg_.mqttPort);
  client_.setBufferSize(kBufferSize);
  client_.setSocketTimeout(kSocketTimeoutS);
  client_.setCallback([this](char* t, uint8_t* p, unsigned int n) { onMessage(t, p, n); });
}

void MqttLink::loop(uint32_t nowMs) {
  if (client_.connected()) {
    client_.loop();
    return;
  }
  if (WiFi.status() != WL_CONNECTED) return;
  if (static_cast<int32_t>(nowMs - nextAttemptMs_) < 0) return;
  tryConnect(nowMs);
}

void MqttLink::tryConnect(uint32_t nowMs) {
  const char* user = cfg_.mqttUser.length() ? cfg_.mqttUser.c_str() : nullptr;
  const char* pass = cfg_.mqttPass.length() ? cfg_.mqttPass.c_str() : nullptr;
  const bool ok = client_.connect(clientId_.c_str(), user, pass, topics_.availability.c_str(),
                                  0, true, "offline");
  if (!ok) {
    const float jitter = (ESP.random() % 1000) / 1000.0f;
    const uint32_t wait = bulb::backoffDelayMs(attempt_, jitter);
    if (attempt_ < 16) ++attempt_;
    nextAttemptMs_ = nowMs + wait;
    Serial.printf("[mqtt] connect failed (state %d), retry in %u ms\n", client_.state(), wait);
    return;
  }

  Serial.println(F("[mqtt] connected"));
  attempt_ = 0;
  client_.publish(topics_.availability.c_str(), "online", true);
  client_.subscribe(topics_.set.c_str());
  client_.subscribe(topics_.haStatus.c_str());
  if (onConnected_) onConnected_();
}

void MqttLink::onMessage(char* topic, uint8_t* payload, unsigned int len) {
  if (topics_.set == topic) {
    if (onCommand_) onCommand_(reinterpret_cast<const char*>(payload), len);
  } else if (topics_.haStatus == topic) {
    // Home Assistant restarted: it has forgotten us, so announce again.
    if (len == 6 && std::memcmp(payload, "online", 6) == 0 && onConnected_) onConnected_();
  }
}

void MqttLink::publishState(const char* json) {
  if (client_.connected()) client_.publish(topics_.state.c_str(), json, true);
}

void MqttLink::publishDiscovery(const char* json) {
  if (client_.connected()) client_.publish(topics_.discovery.c_str(), json, true);
}

void MqttLink::publishError(const char* reason) {
  if (client_.connected()) client_.publish(topics_.error.c_str(), reason, false);
}
