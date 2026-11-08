#pragma once
// Build-variant hardware profile. Pins come from build flags in
// platformio.ini; -1 means the channel is not fitted.

#include "LightState.h"

#ifndef PIN_R
#define PIN_R -1
#endif
#ifndef PIN_G
#define PIN_G -1
#endif
#ifndef PIN_B
#define PIN_B -1
#endif
#ifndef PIN_CW
#define PIN_CW -1
#endif
#ifndef PIN_WW
#define PIN_WW -1
#endif
#ifndef BULB_MODEL
#define BULB_MODEL "Smart Bulb"
#endif

namespace profile {

constexpr int kPinR = PIN_R;
constexpr int kPinG = PIN_G;
constexpr int kPinB = PIN_B;
constexpr int kPinCw = PIN_CW;
constexpr int kPinWw = PIN_WW;
constexpr const char* kModel = BULB_MODEL;

static_assert(kPinR >= 0 || kPinCw >= 0 || kPinWw >= 0,
              "Define at least one LED channel pin in platformio.ini");
static_assert((kPinR >= 0) == (kPinG >= 0) && (kPinG >= 0) == (kPinB >= 0),
              "PIN_R, PIN_G and PIN_B must be defined together");

inline bulb::Capabilities capabilities() {
  bulb::Capabilities c;
  c.rgb = kPinR >= 0;
  c.coolWhite = kPinCw >= 0;
  c.warmWhite = kPinWw >= 0;
  return c;
}

}  // namespace profile
