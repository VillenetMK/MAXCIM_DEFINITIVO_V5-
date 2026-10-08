#include "control_core.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <string>

using maxcim::Command;
using maxcim::Controller;

static std::string frame(const char* payload) {
  char buffer[256];
  snprintf(buffer, sizeof(buffer), "%s*%04X", payload,
           maxcim::crc16(payload, strlen(payload)));
  return buffer;
}

static bool parse(const char* payload, Command& command) {
  char buffer[256];
  const std::string encoded = frame(payload);
  snprintf(buffer, sizeof(buffer), "%s", encoded.c_str());
  return maxcim::parse_frame(buffer, command);
}

static maxcim::Config config(bool wired = true, bool calibrated = true) {
  const maxcim::Config value = {
    wired, calibrated, 1000.0f, 1200.0f, 1.0f, 0.5f, 600, 300.0f, 2000
  };
  return value;
}

static Controller::Result send(Controller& control, const char* payload,
                               uint32_t now) {
  Command command;
  assert(parse(payload, command));
  return control.handle(command, now);
}

static void protocol_validation() {
  assert(maxcim::crc16("123456789", 9) == 0x29B1);
  Command command;
  assert(parse("H 42", command));
  assert(command.type == 'H' && command.session == 42);
  assert(parse("V 42 1 12.5 2e1", command));
  assert(command.left == 12.5f && command.right == 20.0f);
  assert(parse("D 42 2 0 150", command));
  assert(parse("S 42 4294967295", command));
  const char* bad[] = {
    "H 0", "H -1", "H 4294967296", "H 42 extra", "S 42 0",
    "S 42 4294967296", "V 42 1 -1 0", "V 42 1 nan 0",
    "V 42 1 inf 0", "V 42 1 1e999 0", "V 42 1 1e39 0",
    "V 42 1 0x10 0", "V 42 1 5watts 0", "V 42 1 . 0",
    "V 42 1 1 2 extra", "V 42 1 1", "D 42 1 151 0",
    "D 42 1 0 -1", "D 42 1 1.5 1", "X 42 1 0 0"
  };
  for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); ++i)
    assert(!parse(bad[i], command));
  char corrupt[] = "H 42*0000";
  assert(!maxcim::parse_frame(corrupt, command));
  char trailing[] = "H 42*00000";
  assert(!maxcim::parse_frame(trailing, command));

  maxcim::LineReader receiver;
  assert(receiver.feed('\0') == maxcim::LineReader::INVALID);
  assert(receiver.feed('\n') == maxcim::LineReader::PENDING);
  for (size_t i = 0; i < maxcim::MAX_LINE_BYTES - 1; ++i)
    assert(receiver.feed('x') == maxcim::LineReader::PENDING);
  assert(receiver.feed('x') == maxcim::LineReader::INVALID);
  assert(receiver.feed('x') == maxcim::LineReader::PENDING);
  assert(receiver.feed('\n') == maxcim::LineReader::PENDING);
  const std::string valid = frame("H 123");
  for (size_t i = 0; i < valid.size(); ++i)
    assert(receiver.feed(valid[i]) == maxcim::LineReader::PENDING);
  assert(receiver.feed('\n') == maxcim::LineReader::READY);
  assert(maxcim::parse_frame(receiver.line(), command));
  assert(command.session == 123);
}

static void gating_and_sessions() {
  Controller unwired(config(false, false));
  unwired.begin(0, 123, 456);
  assert((unwired.flags() & 9) == 9);
  assert(send(unwired, "H 10", 0) == Controller::HELLO);
  assert(send(unwired, "D 10 1 100 100", 1) == Controller::REJECTED);
  assert(unwired.ack_sequence() == 0);
  assert(send(unwired, "S 10 1", 2) == Controller::ACCEPTED);
  assert(unwired.left_permille() == 0);

  Controller bench(config(true, false));
  bench.begin(0, 0, 0);
  send(bench, "H 20", 0);
  assert(send(bench, "V 20 1 20 20", 1) == Controller::REJECTED);
  assert(send(bench, "D 20 1 150 0", 2) == Controller::ACCEPTED);
  bench.update(50, 1, 0);
  assert(bench.left_permille() == 15 && bench.right_permille() == 0);
  // Replays and wrong-session commands stop, but do not advance ack.
  assert(send(bench, "D 20 1 150 0", 60) == Controller::REJECTED);
  assert(bench.left_permille() == 0 && bench.ack_sequence() == 1);
  assert(send(bench, "D 99 2 150 0", 61) == Controller::REJECTED);
  assert(bench.ack_sequence() == 1);
  assert(send(bench, "D 20 2 150 0", 70) == Controller::ACCEPTED);
  bench.update(100, 2, 0);
  assert(bench.left_permille() > 0);
  assert(send(bench, "H 21", 110) == Controller::HELLO);
  assert(bench.left_permille() == 0 && bench.ack_sequence() == 0);
  assert(bench.session() == 21);
  bench.update(150, 3, 0);
  assert(bench.left_pps() == 20); // H did not reset physical tick baseline.
}

static void emergency_stops_and_watchdog() {
  Controller control(config());
  control.begin(0, 0, 0);
  send(control, "H 1", 0);
  send(control, "V 1 1 100 100", 1);
  control.update(50, 1, 1);
  assert(control.left_permille() == 15);
  control.invalid_frame();
  assert(control.left_permille() == 0 && control.right_permille() == 0);
  control.poll_watchdog(350);
  assert(!(control.flags() & maxcim::WATCHDOG_EXPIRED));
  control.poll_watchdog(351);
  assert(control.flags() & maxcim::WATCHDOG_EXPIRED);
  assert(control.ack_sequence() == 1);

  send(control, "V 1 2 100 100", 400);
  control.update(450, 2, 2);
  assert(control.left_permille() > 0);
  send(control, "V 1 3 0 100", 451);
  assert(control.left_permille() == 0);
  assert(control.right_permille() > 0);
  send(control, "S 1 4", 452);
  assert(control.left_permille() == 0 && control.right_permille() == 0);
  assert(control.ack_sequence() == 4);
}

static void feedback_ramp_and_saturation() {
  Controller control(config());
  control.begin(0, 0, 0);
  send(control, "H 1", 0);
  uint32_t count = 0;
  uint16_t previous_duty = 0;
  for (uint32_t i = 1; i <= 100; ++i) {
    char command[80];
    snprintf(command, sizeof(command), "V 1 %lu 5000 5000",
             static_cast<unsigned long>(i));
    send(control, command, i * 50 - 1);
    ++count;
    control.update(i * 50, count, count);
    assert(control.left_permille() <= 600);
    assert(control.left_permille() <= previous_duty + 15);
    previous_duty = control.left_permille();
  }
  assert(control.left_permille() == 600);
  assert(control.left_pps() == 20.0f);
  // Following sustained saturation, excess measured speed must reduce duty.
  send(control, "V 1 101 20 20", 5001);
  control.update(5050, count + 100, count + 100);
  assert(control.left_permille() == 585);
  assert(control.right_permille() == 585);

  Controller bench(config(true, false));
  bench.begin(0, 0, 0);
  send(bench, "H 2", 0);
  for (uint32_t i = 1; i <= 20; ++i) {
    char command[80];
    snprintf(command, sizeof(command), "D 2 %lu 150 50",
             static_cast<unsigned long>(i));
    send(bench, command, i * 50 - 1);
    bench.update(i * 50, i, i);
    assert(bench.left_permille() <= 150 && bench.right_permille() <= 50);
  }
  assert(bench.left_permille() == 150 && bench.right_permille() == 50);
}

static void stall_is_per_wheel_and_latched() {
  Controller control(config());
  control.begin(0, 0, 0);
  send(control, "H 123", 0);
  for (uint32_t i = 1; i <= 41; ++i) {
    char command[80];
    snprintf(command, sizeof(command), "D 123 %lu 100 100",
             static_cast<unsigned long>(i));
    assert(send(control, command, i * 50 - 1) == Controller::ACCEPTED);
    control.update(i * 50, i, 0); // Left turning, right has no FG.
    if (i < 41) assert(!(control.flags() & maxcim::STALL_LATCHED));
  }
  assert(control.flags() & maxcim::STALL_LATCHED);
  assert(control.left_permille() == 0 && control.right_permille() == 0);
  send(control, "H 124", 2051);
  assert(control.flags() & maxcim::STALL_LATCHED);
  assert(send(control, "D 124 1 100 100", 2052) == Controller::REJECTED);
  assert(send(control, "S 124 1", 2053) == Controller::ACCEPTED);
  assert(control.flags() & maxcim::STALL_LATCHED);
}

static void modular_clock_and_ticks() {
  Controller control(config());
  const uint32_t start = UINT32_MAX - 25U;
  control.begin(start, UINT32_MAX - 2U, UINT32_MAX - 2U);
  send(control, "H 1", start);
  send(control, "V 1 1 100 100", start);
  const uint32_t sample = start + 50U;
  control.update(sample, 2U, 2U);
  assert(control.left_pps() == 100.0f); // Five real pulses over rollover.
  assert(control.left_permille() == 15);
  control.poll_watchdog(start + 349U);
  assert(!(control.flags() & maxcim::WATCHDOG_EXPIRED));
  control.poll_watchdog(start + 350U);
  assert(control.flags() & maxcim::WATCHDOG_EXPIRED);
  assert(control.left_permille() == 0);
}

int main() {
  protocol_validation();
  gating_and_sessions();
  emergency_stops_and_watchdog();
  feedback_ramp_and_saturation();
  stall_is_per_wheel_and_latched();
  modular_clock_and_ticks();
  puts("PASS: CRC/parser, line bounds, gating, sessions/replays, watchdog, "
       "individual stops, measured FG/PI/ramp, saturation, latched stall, "
       "uint32 rollover");
  return 0;
}
