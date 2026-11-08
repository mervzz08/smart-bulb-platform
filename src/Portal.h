#pragma once
// First-time setup: the bulb opens a WPA2 access point ("Bulb-xxxxxx") with a
// captive portal where the user enters Wi-Fi and MQTT broker details.

#include <DNSServer.h>
#include <ESP8266WebServer.h>

#include "Storage.h"

class Portal {
 public:
  void begin(const String& apSsid, const NetConfig& current);
  // Returns true once valid settings have been submitted; read them via result().
  bool loop();
  const NetConfig& result() const { return result_; }

 private:
  void handleRoot();
  void handleSave();
  void redirectToRoot();
  String page(const String& message) const;
  void scan();

  ESP8266WebServer server_{80};
  DNSServer dns_;
  NetConfig current_;
  NetConfig result_;
  String networks_;  // pre-rendered <option> list
  bool done_ = false;
};
