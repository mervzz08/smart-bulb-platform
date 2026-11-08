// Host-side unit tests for lib/bulb_core. Run with: pio test -e native

#include <unity.h>

#include <cstring>
#include <string>

#include "ColorMath.h"
#include "Command.h"
#include "Network.h"

using namespace bulb;

namespace {

Capabilities rgbw() {  // RGB + one fixed cool-white channel
  Capabilities c;
  c.rgb = true;
  c.coolWhite = true;
  return c;
}

Capabilities rgbcct() {  // RGB + cool + warm (tunable white)
  Capabilities c = rgbw();
  c.warmWhite = true;
  return c;
}

CommandResult send(LightState& s, const char* json, const Capabilities& caps) {
  return applyCommand(s, json, std::strlen(json), caps);
}

}  // namespace

void setUp() {}
void tearDown() {}

// ---- defaults & channel mapping -------------------------------------------

void test_default_state_matches_hardware() {
  TEST_ASSERT_EQUAL(static_cast<int>(ColorMode::White), static_cast<int>(defaultState(rgbw()).mode));
  const LightState t = defaultState(rgbcct());
  TEST_ASSERT_EQUAL(static_cast<int>(ColorMode::ColorTemp), static_cast<int>(t.mode));
  TEST_ASSERT_TRUE(t.mireds > 153 && t.mireds < 370);
}

void test_off_produces_dark_channels() {
  LightState s;
  s.on = false;
  const Channels c = computeChannels(s, rgbcct());
  TEST_ASSERT_EQUAL_FLOAT(0, c.r + c.g + c.b + c.cw + c.ww);
}

void test_rgb_scales_with_brightness() {
  LightState s;
  s.mode = ColorMode::Rgb;
  s.r = 255; s.g = 0; s.b = 51;
  s.brightness = 51;  // 20%
  const Channels c = computeChannels(s, rgbw());
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.2f, c.r);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, c.g);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.04f, c.b);
  TEST_ASSERT_EQUAL_FLOAT(0, c.cw);
}

void test_color_temp_mixes_white_channels() {
  LightState s = defaultState(rgbcct());
  s.mireds = 153;
  Channels c = computeChannels(s, rgbcct());
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, c.cw);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, c.ww);

  s.mireds = 370;
  c = computeChannels(s, rgbcct());
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, c.cw);
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, c.ww);

  s.mireds = (153 + 370) / 2;
  c = computeChannels(s, rgbcct());
  TEST_ASSERT_FLOAT_WITHIN(0.01f, 1.0f, c.cw + c.ww);  // constant total power
  TEST_ASSERT_FLOAT_WITHIN(0.01f, c.cw, c.ww);
}

void test_color_temp_falls_back_on_fixed_white() {
  LightState s;
  s.mode = ColorMode::ColorTemp;
  const Channels c = computeChannels(s, rgbw());
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, c.cw);
}

void test_gamma_and_pulse_curves() {
  TEST_ASSERT_EQUAL_FLOAT(0.0f, gammaCorrect(0.0f));
  TEST_ASSERT_EQUAL_FLOAT(1.0f, gammaCorrect(1.0f));
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.2176f, gammaCorrect(0.5f));
  TEST_ASSERT_EQUAL_FLOAT(0.0f, gammaCorrect(-1.0f));  // clamped
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, pulseLevel(0, 2000));
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 1.0f, pulseLevel(1000, 2000));
  TEST_ASSERT_FLOAT_WITHIN(0.001f, 0.0f, pulseLevel(4000, 2000));
}

// ---- command protocol -----------------------------------------------------

void test_state_on_off_toggle() {
  LightState s;
  TEST_ASSERT_TRUE(send(s, R"({"state":"OFF"})", rgbw()).ok);
  TEST_ASSERT_FALSE(s.on);
  TEST_ASSERT_TRUE(send(s, R"({"state":"TOGGLE"})", rgbw()).ok);
  TEST_ASSERT_TRUE(s.on);
  TEST_ASSERT_FALSE(send(s, R"({"state":"maybe"})", rgbw()).ok);
}

void test_brightness_zero_turns_off_but_remembers_level() {
  LightState s;
  s.brightness = 200;
  TEST_ASSERT_TRUE(send(s, R"({"brightness":0})", rgbw()).ok);
  TEST_ASSERT_FALSE(s.on);
  TEST_ASSERT_EQUAL_UINT8(200, s.brightness);
}

void test_color_sets_rgb_mode_and_turns_on() {
  LightState s;
  s.on = false;
  const CommandResult r = send(s, R"({"color":{"r":255,"g":80,"b":0},"brightness":300})", rgbw());
  TEST_ASSERT_TRUE(r.ok);
  TEST_ASSERT_TRUE(s.on);
  TEST_ASSERT_EQUAL(static_cast<int>(ColorMode::Rgb), static_cast<int>(s.mode));
  TEST_ASSERT_EQUAL_UINT8(80, s.g);
  TEST_ASSERT_EQUAL_UINT8(255, s.brightness);  // clamped from 300
  TEST_ASSERT_EQUAL_UINT32(kDefaultTransitionMs, r.transitionMs);
}

void test_color_temp_is_clamped_to_hardware_range() {
  LightState s = defaultState(rgbcct());
  TEST_ASSERT_TRUE(send(s, R"({"color_temp":1000})", rgbcct()).ok);
  TEST_ASSERT_EQUAL_UINT16(370, s.mireds);
  TEST_ASSERT_TRUE(send(s, R"({"color_temp":10})", rgbcct()).ok);
  TEST_ASSERT_EQUAL_UINT16(153, s.mireds);
}

void test_color_temp_rejected_without_tunable_white() {
  LightState s;
  const CommandResult r = send(s, R"({"color_temp":250})", rgbw());
  TEST_ASSERT_FALSE(r.ok);
  TEST_ASSERT_NOT_NULL(r.error);
}

void test_white_command_on_fixed_white_bulb() {
  LightState s;
  s.mode = ColorMode::Rgb;
  TEST_ASSERT_TRUE(send(s, R"({"white":128})", rgbw()).ok);
  TEST_ASSERT_EQUAL(static_cast<int>(ColorMode::White), static_cast<int>(s.mode));
  TEST_ASSERT_EQUAL_UINT8(128, s.brightness);
}

void test_invalid_commands_leave_state_untouched() {
  const LightState before = defaultState(rgbcct());
  const char* bad[] = {
      "not json",
      "[1,2,3]",
      R"({"brightness":"high"})",
      R"({"color":{"r":1,"g":2}})",
      R"({"color":{"r":1,"g":2,"b":3},"color_temp":200})",
      R"({"brightness":10,"effect":"disco"})",  // valid field + invalid field
      R"({"transition":-1})",
  };
  for (const char* json : bad) {
    LightState s = before;
    TEST_ASSERT_FALSE_MESSAGE(send(s, json, rgbcct()).ok, json);
    TEST_ASSERT_EQUAL_UINT8_MESSAGE(before.brightness, s.brightness, json);
    TEST_ASSERT_EQUAL_MESSAGE(before.on, s.on, json);
  }
}

void test_transition_and_effect() {
  LightState s;
  CommandResult r = send(s, R"({"transition":1.5,"effect":"pulse"})", rgbw());
  TEST_ASSERT_TRUE(r.ok);
  TEST_ASSERT_EQUAL_UINT32(1500, r.transitionMs);
  TEST_ASSERT_EQUAL(static_cast<int>(Effect::Pulse), static_cast<int>(s.effect));
  r = send(s, R"({"transition":3600})", rgbw());
  TEST_ASSERT_EQUAL_UINT32(kMaxTransitionMs, r.transitionMs);
}

void test_state_round_trips_through_json() {
  LightState a = defaultState(rgbcct());
  a.on = false;
  a.brightness = 42;
  a.mode = ColorMode::Rgb;
  a.r = 10; a.g = 20; a.b = 30;
  a.mireds = 300;
  a.effect = Effect::Pulse;

  char buf[256];
  const size_t n = writeState(a, rgbcct(), buf, sizeof buf);
  TEST_ASSERT_TRUE(n > 0);

  LightState b = defaultState(rgbcct());
  TEST_ASSERT_TRUE(restoreState(b, buf, n, rgbcct()));
  TEST_ASSERT_EQUAL(a.on, b.on);
  TEST_ASSERT_EQUAL_UINT8(a.brightness, b.brightness);
  TEST_ASSERT_EQUAL(static_cast<int>(a.mode), static_cast<int>(b.mode));
  TEST_ASSERT_EQUAL_UINT8(a.b, b.b);
  TEST_ASSERT_EQUAL_UINT16(a.mireds, b.mireds);
  TEST_ASSERT_EQUAL(static_cast<int>(a.effect), static_cast<int>(b.effect));
}

void test_write_state_reports_small_buffer() {
  char tiny[8];
  TEST_ASSERT_EQUAL_size_t(0, writeState(LightState{}, rgbcct(), tiny, sizeof tiny));
}

// ---- network helpers ------------------------------------------------------

void test_topics_layout() {
  const Topics t = makeTopics("a1b2c3");
  TEST_ASSERT_EQUAL_STRING("bulb/a1b2c3/set", t.set.c_str());
  TEST_ASSERT_EQUAL_STRING("bulb/a1b2c3/availability", t.availability.c_str());
  TEST_ASSERT_EQUAL_STRING("homeassistant/light/a1b2c3/config", t.discovery.c_str());
}

void test_discovery_advertises_only_supported_modes() {
  const Topics t = makeTopics("a1b2c3");
  const DeviceInfo d{"a1b2c3", "Test bulb", "RGBCCT", "1.0.0"};
  char buf[900];

  TEST_ASSERT_TRUE(writeDiscovery(d, t, rgbcct(), buf, sizeof buf) > 0);
  std::string tunable(buf);
  TEST_ASSERT_TRUE(tunable.find(R"("supported_color_modes":["rgb","color_temp"])") != std::string::npos);
  TEST_ASSERT_TRUE(tunable.find("min_mireds") != std::string::npos);

  TEST_ASSERT_TRUE(writeDiscovery(d, t, rgbw(), buf, sizeof buf) > 0);
  std::string fixed(buf);
  TEST_ASSERT_TRUE(fixed.find(R"("supported_color_modes":["rgb","white"])") != std::string::npos);
  TEST_ASSERT_TRUE(fixed.find("min_mireds") == std::string::npos);
}

void test_backoff_grows_and_caps() {
  TEST_ASSERT_EQUAL_UINT32(750, backoffDelayMs(0, 0.0f));
  TEST_ASSERT_EQUAL_UINT32(2000, backoffDelayMs(1, 0.5f));
  TEST_ASSERT_EQUAL_UINT32(4000, backoffDelayMs(2, 0.5f));
  TEST_ASSERT_EQUAL_UINT32(60000, backoffDelayMs(10, 0.99f));
  TEST_ASSERT_EQUAL_UINT32(60000, backoffDelayMs(255, 0.5f));
}

namespace {
uint32_t seq[] = {0xFFFFFFFFu, 0, 1, 30, 31, 62};  // first value must be rejected
size_t seqPos = 0;
uint32_t fakeRng() { return seq[seqPos++ % (sizeof seq / sizeof seq[0])]; }
}  // namespace

void test_password_generation() {
  seqPos = 0;
  const std::string p = makePassword(5, fakeRng);
  TEST_ASSERT_EQUAL_STRING("ab9aa", p.c_str());  // 0->a, 1->b, 30->9, 31->a, 62->a

  const std::string alphabet = "abcdefghjkmnpqrstuvwxyz23456789";
  seqPos = 0;
  const std::string longer = makePassword(32, fakeRng);
  TEST_ASSERT_EQUAL_size_t(32, longer.size());
  for (char c : longer) TEST_ASSERT_TRUE(alphabet.find(c) != std::string::npos);
}

int main(int, char**) {
  UNITY_BEGIN();
  RUN_TEST(test_default_state_matches_hardware);
  RUN_TEST(test_off_produces_dark_channels);
  RUN_TEST(test_rgb_scales_with_brightness);
  RUN_TEST(test_color_temp_mixes_white_channels);
  RUN_TEST(test_color_temp_falls_back_on_fixed_white);
  RUN_TEST(test_gamma_and_pulse_curves);
  RUN_TEST(test_state_on_off_toggle);
  RUN_TEST(test_brightness_zero_turns_off_but_remembers_level);
  RUN_TEST(test_color_sets_rgb_mode_and_turns_on);
  RUN_TEST(test_color_temp_is_clamped_to_hardware_range);
  RUN_TEST(test_color_temp_rejected_without_tunable_white);
  RUN_TEST(test_white_command_on_fixed_white_bulb);
  RUN_TEST(test_invalid_commands_leave_state_untouched);
  RUN_TEST(test_transition_and_effect);
  RUN_TEST(test_state_round_trips_through_json);
  RUN_TEST(test_write_state_reports_small_buffer);
  RUN_TEST(test_topics_layout);
  RUN_TEST(test_discovery_advertises_only_supported_modes);
  RUN_TEST(test_backoff_grows_and_caps);
  RUN_TEST(test_password_generation);
  return UNITY_END();
}
