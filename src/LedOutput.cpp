#include "LedOutput.h"

#include "ColorMath.h"
#include "Profile.h"

namespace {
constexpr int kPins[5] = {profile::kPinR, profile::kPinG, profile::kPinB,
                          profile::kPinCw, profile::kPinWw};
}

void LedOutput::begin() {
  analogWriteRange(kPwmRange);
  analogWriteFreq(kPwmFreqHz);
  for (int pin : kPins) {
    if (pin < 0) continue;
    pinMode(pin, OUTPUT);
    analogWrite(pin, 0);
  }
}

void LedOutput::setTarget(const bulb::Channels& target, uint32_t transitionMs) {
  from_ = current_;  // start from wherever a running fade has got to
  to_ = target;
  startMs_ = millis();
  durationMs_ = transitionMs;
  if (transitionMs == 0) current_ = target;
}

void LedOutput::update(uint32_t nowMs) {
  if (durationMs_ > 0) {
    const uint32_t elapsed = nowMs - startMs_;  // wrap-safe unsigned math
    if (elapsed >= durationMs_) {
      current_ = to_;
      durationMs_ = 0;
    } else {
      current_ = bulb::lerp(from_, to_, static_cast<float>(elapsed) / durationMs_);
    }
  }

  bulb::Channels out = current_;
  if (effect_ == bulb::Effect::Pulse) {
    const float k = kPulseFloor + (1.0f - kPulseFloor) * bulb::pulseLevel(nowMs, kPulsePeriodMs);
    out.r *= k;
    out.g *= k;
    out.b *= k;
    out.cw *= k;
    out.ww *= k;
  }
  write(out);
}

void LedOutput::write(const bulb::Channels& c) {
  writePin(kPins[0], c.r, lastDuty_[0]);
  writePin(kPins[1], c.g, lastDuty_[1]);
  writePin(kPins[2], c.b, lastDuty_[2]);
  writePin(kPins[3], c.cw, lastDuty_[3]);
  writePin(kPins[4], c.ww, lastDuty_[4]);
}

void LedOutput::writePin(int pin, float linear, uint16_t& last) {
  if (pin < 0) return;
  const uint16_t duty = static_cast<uint16_t>(bulb::gammaCorrect(linear) * kPwmRange + 0.5f);
  if (duty == last) return;  // skip redundant writes; analogWrite restarts the PWM cycle
  analogWrite(pin, duty);
  last = duty;
}
