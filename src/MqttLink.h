#pragma once
// MQTT session: non-blocking reconnect with exponential backoff, retained
// availability via Last Will, and Home Assistant discovery republishing.

#include <ESP8266WiFi.h>
#include <PubSubClient.h>

#include <functional>

#include "Network.h"
#include "Storage.h"

class MqttLink {
 public:
  using CommandHandler = std::function<void(const char* payload, size_t len)>;
  using ConnectedHandler = std::function<void()>;

  void begin(const NetConfig& cfg, const bulb::Topics& topics, const String& clientId,
             CommandHandler onCommand, ConnectedHandler onConnected);
  void loop(uint32_t nowMs);

  void publishState(const char* json);
  void publishDiscovery(const char* json);
  void publishError(const char* reason);

 private:
  void tryConnect(uint32_t nowMs);
  void onMessage(char* topic, uint8_t* payload, unsigned int len);

  WiFiClient net_;
  PubSubClient client_{net_};
  NetConfig cfg_;  // owns the host string PubSubClient points at
  bulb::Topics topics_;
  String clientId_;
  CommandHandler onCommand_;
  ConnectedHandler onConnected_;
  uint8_t attempt_ = 0;
  uint32_t nextAttemptMs_ = 0;
};
