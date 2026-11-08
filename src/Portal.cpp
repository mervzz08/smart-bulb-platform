#include "Portal.h"

#include <ESP8266WiFi.h>

namespace {

const IPAddress kApIp(192, 168, 4, 1);
const IPAddress kApMask(255, 255, 255, 0);

// Everything user- or network-supplied is escaped before it reaches HTML:
// a neighbour's SSID is attacker-controlled text.
String htmlEscape(const String& in) {
  String out;
  out.reserve(in.length());
  for (char c : in) {
    switch (c) {
      case '&': out += F("&amp;"); break;
      case '<': out += F("&lt;"); break;
      case '>': out += F("&gt;"); break;
      case '"': out += F("&quot;"); break;
      case '\'': out += F("&#39;"); break;
      default: out += c;
    }
  }
  return out;
}

}  // namespace

void Portal::begin(const String& apSsid, const NetConfig& current) {
  current_ = current;
  WiFi.persistent(false);
  WiFi.mode(WIFI_AP_STA);  // STA side is only used for scanning
  scan();
  WiFi.softAPConfig(kApIp, kApIp, kApMask);
  WiFi.softAP(apSsid.c_str(), current.apPass.c_str());

  dns_.start(53, "*", kApIp);  // resolve every name to us -> phone shows the portal
  server_.on("/", HTTP_GET, [this] { handleRoot(); });
  server_.on("/save", HTTP_POST, [this] { handleSave(); });
  server_.on("/rescan", HTTP_GET, [this] { scan(); redirectToRoot(); });
  server_.onNotFound([this] { redirectToRoot(); });
  server_.begin();
}

bool Portal::loop() {
  dns_.processNextRequest();
  server_.handleClient();
  return done_;
}

void Portal::scan() {
  const int n = WiFi.scanNetworks();
  networks_ = "";
  for (int i = 0; i < n; ++i) {
    const String ssid = WiFi.SSID(i);
    if (ssid.isEmpty()) continue;
    networks_ += F("<option value=\"");
    networks_ += htmlEscape(ssid);
    networks_ += F("\">");
  }
  WiFi.scanDelete();
}

void Portal::redirectToRoot() {
  server_.sendHeader(F("Location"), String(F("http://")) + kApIp.toString() + "/");
  server_.send(302, F("text/plain"), "");
}

void Portal::handleRoot() { server_.send(200, F("text/html"), page("")); }

void Portal::handleSave() {
  NetConfig cfg = current_;  // keeps apPass
  cfg.ssid = server_.arg("ssid");
  cfg.wifiPass = server_.arg("wifi_pass");
  cfg.mqttHost = server_.arg("mqtt_host");
  cfg.mqttUser = server_.arg("mqtt_user");
  const String mqttPass = server_.arg("mqtt_pass");
  if (mqttPass.length()) cfg.mqttPass = mqttPass;  // blank = keep the saved one
  const long port = server_.arg("mqtt_port").toInt();

  cfg.ssid.trim();
  cfg.mqttHost.trim();

  String error;
  if (cfg.ssid.isEmpty() || cfg.ssid.length() > 32) error = F("Wi-Fi name must be 1-32 characters.");
  else if (cfg.wifiPass.length() && (cfg.wifiPass.length() < 8 || cfg.wifiPass.length() > 63))
    error = F("Wi-Fi password must be 8-63 characters (or blank for an open network).");
  else if (cfg.mqttHost.isEmpty()) error = F("MQTT broker address is required.");
  else if (port < 1 || port > 65535) error = F("MQTT port must be 1-65535.");

  if (error.length()) {
    server_.send(400, F("text/html"), page(error));
    return;
  }

  cfg.mqttPort = static_cast<uint16_t>(port);
  result_ = cfg;
  server_.send(200, F("text/html"),
               F("<!doctype html><meta name=viewport content='width=device-width'>"
                 "<body style='font-family:sans-serif;padding:24px'>"
                 "<h2>Saved</h2><p>The bulb is restarting and joining your network.</p>"));
  done_ = true;
}

String Portal::page(const String& message) const {
  String h;
  h.reserve(2200 + networks_.length());
  h += F("<!doctype html><html><head><meta charset=utf-8>"
         "<meta name=viewport content='width=device-width,initial-scale=1'>"
         "<title>Bulb setup</title><style>"
         "body{font-family:sans-serif;max-width:420px;margin:auto;padding:16px;color:#222}"
         "label{display:block;margin-top:12px;font-size:14px}"
         "input{width:100%;padding:8px;box-sizing:border-box;font-size:16px}"
         "button{margin-top:20px;width:100%;padding:12px;font-size:16px}"
         ".err{background:#fde8e8;color:#9b1c1c;padding:8px;border-radius:4px}"
         "fieldset{border:1px solid #ddd;margin-top:16px}"
         "</style></head><body><h2>Bulb setup</h2>");
  if (message.length()) {
    h += F("<p class=err>");
    h += htmlEscape(message);
    h += F("</p>");
  }
  h += F("<form method=post action=/save><fieldset><legend>Wi-Fi</legend>"
         "<label>Network name<input name=ssid list=nets required maxlength=32 value=\"");
  h += htmlEscape(current_.ssid);
  h += F("\"></label><datalist id=nets>");
  h += networks_;
  h += F("</datalist><a href=/rescan>Rescan</a>"
         "<label>Password<input name=wifi_pass type=password maxlength=63></label>"
         "</fieldset><fieldset><legend>MQTT broker</legend>"
         "<label>Address<input name=mqtt_host required value=\"");
  h += htmlEscape(current_.mqttHost);
  h += F("\"></label><label>Port<input name=mqtt_port type=number min=1 max=65535 value=\"");
  h += String(current_.mqttPort);
  h += F("\"></label><label>Username (optional)<input name=mqtt_user value=\"");
  h += htmlEscape(current_.mqttUser);
  h += F("\"></label><label>Password (optional");
  if (current_.mqttPass.length()) h += F(", blank keeps current");
  h += F(")<input name=mqtt_pass type=password></label>"
         "</fieldset><button>Save and connect</button></form></body></html>");
  return h;
}
