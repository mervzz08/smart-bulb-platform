#pragma once
// Non-blocking PWM output with timed fades and a breathing effect.
// update() is called every loop; nothing here ever calls delay().

#include <Arduino.h>

#include "LightState.h"

class LedOutput {
 public:
  void begin();
  void setTarget(const bulb::Channels& target, uint32_t transitionMs);
  void setEffect(bulb::Effect effect) { effect_ = effect; }
  void update(uint32_t nowMs);

 private:
  static constexpr uint16_t kPwmRange = 1023;
  static constexpr uint32_t kPwmFreqHz = 2000;  // above visible/camera flicker for most people
  static constexpr uint32_t kPulsePeriodMs = 2000;
  static constexpr float kPulseFloor = 0.1f;  // never fully dark mid-pulse

  void write(const bulb::Channels& c);
  void writePin(int pin, float linear, uint16_t& last);

  bulb::Channels from_, to_, current_;
  uint32_t startMs_ = 0;
  uint32_t durationMs_ = 0;
  bulb::Effect effect_ = bulb::Effect::None;
  uint16_t lastDuty_[5] = {UINT16_MAX, UINT16_MAX, UINT16_MAX, UINT16_MAX, UINT16_MAX};
};
