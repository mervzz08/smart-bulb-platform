#include "ColorMath.h"

#include <cmath>

namespace bulb {

namespace {
constexpr float kTwoPi = 6.28318530718f;

float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }
}  // namespace

LightState defaultState(const Capabilities& caps) {
  LightState s;
  if (caps.tunableWhite()) {
    s.mode = ColorMode::ColorTemp;
    s.mireds = static_cast<uint16_t>((caps.minMireds + caps.maxMireds) / 2);
  } else if (caps.anyWhite()) {
    s.mode = ColorMode::White;
  } else {
    s.mode = ColorMode::Rgb;
  }
  return s;
}

float warmFraction(uint16_t mireds, const Capabilities& caps) {
  if (caps.maxMireds <= caps.minMireds) return 0.0f;
  return clamp01(static_cast<float>(mireds - caps.minMireds) /
                 static_cast<float>(caps.maxMireds - caps.minMireds));
}

Channels computeChannels(const LightState& s, const Capabilities& caps) {
  Channels c;
  if (!s.on) return c;

  const float k = s.brightness / 255.0f;
  ColorMode mode = s.mode;
  if (mode == ColorMode::Rgb && !caps.rgb) mode = ColorMode::White;
  if (mode == ColorMode::ColorTemp && !caps.tunableWhite()) mode = ColorMode::White;

  switch (mode) {
    case ColorMode::Rgb:
      c.r = s.r / 255.0f * k;
      c.g = s.g / 255.0f * k;
      c.b = s.b / 255.0f * k;
      break;

    case ColorMode::ColorTemp: {
      // Constant total power across the range: the two white channels always
      // sum to the requested brightness, so the bulb never exceeds its rating.
      const float t = warmFraction(s.mireds, caps);
      c.cw = (1.0f - t) * k;
      c.ww = t * k;
      break;
    }

    case ColorMode::White:
      if (caps.tunableWhite()) {
        c.cw = c.ww = 0.5f * k;
      } else if (caps.coolWhite) {
        c.cw = k;
      } else if (caps.warmWhite) {
        c.ww = k;
      } else {
        c.r = c.g = c.b = k;  // RGB-only bulb: approximate white
      }
      break;
  }
  return c;
}

float gammaCorrect(float linear, float gamma) {
  return std::pow(clamp01(linear), gamma);
}

float pulseLevel(uint32_t nowMs, uint32_t periodMs) {
  if (periodMs == 0) return 1.0f;
  const float phase = static_cast<float>(nowMs % periodMs) / static_cast<float>(periodMs);
  return 0.5f - 0.5f * std::cos(kTwoPi * phase);
}

Channels lerp(const Channels& a, const Channels& b, float t) {
  t = clamp01(t);
  Channels c;
  c.r = a.r + (b.r - a.r) * t;
  c.g = a.g + (b.g - a.g) * t;
  c.b = a.b + (b.b - a.b) * t;
  c.cw = a.cw + (b.cw - a.cw) * t;
  c.ww = a.ww + (b.ww - a.ww) * t;
  return c;
}

}  // namespace bulb
