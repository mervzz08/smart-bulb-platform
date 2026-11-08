#pragma once

#include "LightState.h"

namespace bulb {

// Sensible power-on state for the given hardware.
LightState defaultState(const Capabilities& caps);

// Map a light state to linear channel levels. Modes the hardware cannot
// render fall back gracefully (e.g. color_temp on a fixed-white bulb).
Channels computeChannels(const LightState& state, const Capabilities& caps);

// 0.0 at the coolest supported temperature, 1.0 at the warmest.
float warmFraction(uint16_t mireds, const Capabilities& caps);

// Perceptual correction: LEDs look far brighter than their duty cycle at low
// levels, so linear values are raised to a power before driving PWM.
float gammaCorrect(float linear, float gamma = 2.2f);

// Smooth 0..1..0 breathing curve with the given period.
float pulseLevel(uint32_t nowMs, uint32_t periodMs);

Channels lerp(const Channels& from, const Channels& to, float t);

}  // namespace bulb
