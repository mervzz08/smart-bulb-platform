#pragma once
// MQTT JSON protocol. Payloads follow the Home Assistant "json" light schema,
// so the bulb works with HA out of the box and with any other MQTT client.

#include <cstddef>
#include <cstdint>

#include "LightState.h"

namespace bulb {

constexpr uint32_t kDefaultTransitionMs = 300;
constexpr uint32_t kMaxTransitionMs = 60000;

struct CommandResult {
  bool ok;
  uint32_t transitionMs;
  const char* error;  // static string, nullptr when ok
};

// Validate and apply a command such as
//   {"state":"ON","brightness":128,"color":{"r":255,"g":80,"b":0},"transition":2}
// All-or-nothing: if any field is invalid, `state` is left untouched.
CommandResult applyCommand(LightState& state, const char* json, size_t len,
                           const Capabilities& caps);

// Serialize the full state (used for the MQTT state topic and for flash).
// Returns bytes written, or 0 if the buffer is too small.
size_t writeState(const LightState& state, const Capabilities& caps, char* out,
                  size_t outSize);

// Restore a state previously produced by writeState. Lenient: unknown or
// unsupported fields are skipped. Returns false only for unparseable input.
bool restoreState(LightState& state, const char* json, size_t len,
                  const Capabilities& caps);

}  // namespace bulb
