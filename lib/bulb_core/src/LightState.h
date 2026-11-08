#pragma once
// Hardware-independent light model. Nothing in lib/bulb_core includes
// Arduino headers, so all of it is unit-tested on the host (pio test -e native).

#include <cstdint>

namespace bulb {

// What the hardware can physically do. Fixed per build variant.
struct Capabilities {
  bool rgb = true;
  bool coolWhite = false;
  bool warmWhite = false;    // coolWhite + warmWhite = tunable white
  uint16_t minMireds = 153;  // ~6500 K (coolest)
  uint16_t maxMireds = 370;  // ~2700 K (warmest)

  bool tunableWhite() const { return coolWhite && warmWhite; }
  bool anyWhite() const { return coolWhite || warmWhite; }
};

enum class ColorMode : uint8_t { Rgb, White, ColorTemp };
enum class Effect : uint8_t { None, Pulse };

struct LightState {
  bool on = true;  // a fresh bulb lights up when first switched on at the wall
  uint8_t brightness = 255;
  ColorMode mode = ColorMode::White;
  uint8_t r = 255, g = 255, b = 255;
  uint16_t mireds = 250;
  Effect effect = Effect::None;
};

// Linear channel levels in 0.0-1.0, before gamma correction.
struct Channels {
  float r = 0, g = 0, b = 0, cw = 0, ww = 0;
};

}  // namespace bulb
