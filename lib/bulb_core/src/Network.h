#pragma once
// Pure helpers for the network layer: topic layout, Home Assistant discovery,
// reconnect backoff and per-device password generation.

#include <cstddef>
#include <cstdint>
#include <string>

#include "LightState.h"

namespace bulb {

struct Topics {
  std::string set;           // commands in
  std::string state;         // retained state out
  std::string availability;  // retained "online"/"offline" (LWT)
  std::string error;         // rejected-command reasons out
  std::string discovery;     // retained Home Assistant config
  std::string haStatus;      // HA birth message; republish discovery on "online"
};

Topics makeTopics(const std::string& deviceId, const std::string& prefix = "bulb",
                  const std::string& discoveryPrefix = "homeassistant");

struct DeviceInfo {
  const char* id;
  const char* name;
  const char* model;
  const char* swVersion;
};

// Home Assistant MQTT discovery payload. Returns bytes written, 0 if too small.
size_t writeDiscovery(const DeviceInfo& dev, const Topics& topics, const Capabilities& caps,
                      char* out, size_t outSize);

// Exponential reconnect delay: base, 2x, 4x ... capped, with +/-25% jitter so a
// house full of bulbs does not hammer the broker in lockstep after an outage.
// jitter01 is a random value in [0, 1).
uint32_t backoffDelayMs(uint8_t attempt, float jitter01, uint32_t baseMs = 1000,
                        uint32_t capMs = 60000);

// Random password from an alphabet without look-alike characters (0/O, 1/l/I).
// Uses rejection sampling so every character is equally likely.
std::string makePassword(size_t length, uint32_t (*rng)());

}  // namespace bulb
