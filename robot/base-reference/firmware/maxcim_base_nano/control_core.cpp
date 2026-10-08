#include "control_core.h"

#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

namespace maxcim {

namespace {

float clamp_value(float value, float low, float high) {
  return value < low ? low : (value > high ? high : value);
}

bool uint32_value(const char* text, uint32_t& value) {
  if (!text || !*text) return false;
  value = 0;
  for (; *text; ++text) {
    if (*text < '0' || *text > '9') return false;
    const uint8_t digit = static_cast<uint8_t>(*text - '0');
    if (value > (UINT32_MAX - digit) / 10U) return false;
    value = value * 10U + digit;
  }
  return true;
}

bool positive_float(const char* text, float& value) {
  if (!text || !*text) return false;
  for (const char* p = text; *p; ++p) {
    if (!((*p >= '0' && *p <= '9') || *p == '.' || *p == '+' ||
          *p == '-' || *p == 'e' || *p == 'E')) return false;
  }
  errno = 0;
  char* end = NULL;
  const double parsed = strtod(text, &end);
  if (end == text || *end || errno == ERANGE || !isfinite(parsed) ||
      parsed < 0.0) return false;
  value = static_cast<float>(parsed);
  return isfinite(value);
}

int hex_digit(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

}  // namespace

uint16_t crc16(const char* data, size_t length) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < length; ++i) {
    crc ^= static_cast<uint16_t>(static_cast<uint8_t>(data[i])) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = crc & 0x8000 ? static_cast<uint16_t>((crc << 1) ^ 0x1021)
                         : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

LineReader::LineReader() : used_(0), discarding_(false) { data_[0] = '\0'; }

LineReader::Result LineReader::feed(char value) {
  if (discarding_) {
    if (value == '\n') discarding_ = false;
    return PENDING;
  }
  if (value == '\n') {
    data_[used_] = '\0';
    const bool empty = used_ == 0;
    used_ = 0;
    return empty ? INVALID : READY;
  }
  // MAX_LINE_BYTES includes the terminating newline. Reject embedded NUL,
  // CR, non-ASCII bytes and overflow immediately, then discard through LF.
  if (value < 32 || value > 126 || used_ >= MAX_LINE_BYTES - 1) {
    used_ = 0;
    discarding_ = true;
    return INVALID;
  }
  data_[used_++] = value;
  return PENDING;
}

bool parse_frame(char* line, Command& command) {
  if (!line) return false;
  const size_t length = strlen(line);
  if (length < 7 || length >= MAX_LINE_BYTES) return false;
  char* separator = strchr(line, '*');
  if (!separator || separator != line + length - 5) return false;
  uint16_t expected = 0;
  for (unsigned i = 1; i <= 4; ++i) {
    const int digit = hex_digit(separator[i]);
    if (digit < 0) return false;
    expected = static_cast<uint16_t>((expected << 4) | digit);
  }
  if (crc16(line, static_cast<size_t>(separator - line)) != expected)
    return false;
  *separator = '\0';
  char* tokens[6];
  unsigned count = 0;
  char* cursor = line;
  while (*cursor) {
    while (*cursor == ' ') ++cursor;
    if (!*cursor) break;
    if (count >= 6) return false;
    tokens[count++] = cursor;
    while (*cursor && *cursor != ' ') ++cursor;
    if (*cursor) *cursor++ = '\0';
  }
  if (count < 2 || strlen(tokens[0]) != 1) return false;
  command.type = tokens[0][0];
  command.sequence = 0;
  command.left = command.right = 0.0f;
  if (!uint32_value(tokens[1], command.session) || command.session == 0)
    return false;
  if (command.type == 'H') return count == 2;
  if (count < 3 || !uint32_value(tokens[2], command.sequence) ||
      command.sequence == 0) return false;
  if (command.type == 'S') return count == 3;
  if (count != 5) return false;
  if (command.type == 'V') {
    return positive_float(tokens[3], command.left) &&
           positive_float(tokens[4], command.right);
  }
  if (command.type == 'D') {
    uint32_t left = 0, right = 0;
    if (!uint32_value(tokens[3], left) || !uint32_value(tokens[4], right) ||
        left > BENCH_LIMIT_PERMILLE || right > BENCH_LIMIT_PERMILLE)
      return false;
    command.left = static_cast<float>(left);
    command.right = static_cast<float>(right);
    return true;
  }
  return false;
}

Controller::Controller(const Config& config)
    : config_(config), mode_(STOPPED), session_(0), sequence_(0),
      last_command_ms_(0), last_sample_ms_(0), watchdog_expired_(true),
      stall_latched_(false) {
  memset(wheels_, 0, sizeof(wheels_));
}

void Controller::begin(uint32_t now, uint32_t left_ticks,
                       uint32_t right_ticks) {
  stop();
  wheels_[0].previous_ticks = left_ticks;
  wheels_[1].previous_ticks = right_ticks;
  last_sample_ms_ = now;
  last_command_ms_ = now;
}

bool Controller::calibrated() const {
  return config_.calibration_confirmed &&
         isfinite(config_.left_pps_at_full_duty) &&
         isfinite(config_.right_pps_at_full_duty) &&
         config_.left_pps_at_full_duty > 0 &&
         config_.right_pps_at_full_duty > 0 && isfinite(config_.kp) &&
         isfinite(config_.ki) && config_.kp >= 0 && config_.ki >= 0;
}

uint8_t Controller::flags() const {
  return (!config_.wiring_confirmed ? WIRING_PENDING : 0) |
         (watchdog_expired_ ? WATCHDOG_EXPIRED : 0) |
         (stall_latched_ ? STALL_LATCHED : 0) |
         (!calibrated() ? CALIBRATION_PENDING : 0);
}

void Controller::stop() {
  mode_ = STOPPED;
  for (unsigned i = 0; i < 2; ++i) {
    wheels_[i].request = 0;
    wheels_[i].duty = 0;
    wheels_[i].integral = 0;
    wheels_[i].stall_armed = false;
  }
}

void Controller::invalid_frame() { stop(); }

void Controller::set_request(unsigned index, float request, uint32_t now) {
  Wheel& wheel = wheels_[index];
  if (request <= 0) {
    wheel.duty = 0;
    wheel.integral = 0;
    wheel.stall_armed = false;
  } else if (wheel.request <= 0) {
    wheel.last_fg_ms = now;
    wheel.stall_armed = false;
    wheel.integral = 0;
  }
  wheel.request = request;
}

Controller::Result Controller::handle(const Command& command, uint32_t now) {
  if (command.type == 'H' && command.session != 0) {
    stop();
    session_ = command.session;
    sequence_ = 0;
    last_command_ms_ = now;
    watchdog_expired_ = false;
    return HELLO;
  }
  if (session_ == 0 || command.session != session_ ||
      command.sequence == 0 || command.sequence <= sequence_) {
    stop();
    return REJECTED;
  }
  if (command.type == 'S') {
    stop();
  } else {
    if (!config_.wiring_confirmed || stall_latched_ ||
        !isfinite(command.left) || !isfinite(command.right) ||
        command.left < 0 || command.right < 0 ||
        (command.type != 'V' && command.type != 'D') ||
        (command.type == 'V' && !calibrated()) ||
        (command.type == 'D' &&
         (command.left > BENCH_LIMIT_PERMILLE ||
          command.right > BENCH_LIMIT_PERMILLE))) {
      stop();
      return REJECTED;
    }
    const Mode next_mode = command.type == 'V' ? VELOCITY : BENCH;
    // Mode changes must not retain integral error or an old bench output.
    if (next_mode != mode_) stop();
    mode_ = next_mode;
    set_request(0, command.left, now);
    set_request(1, command.right, now);
  }
  sequence_ = command.sequence;
  last_command_ms_ = now;
  watchdog_expired_ = false;
  return ACCEPTED;
}

void Controller::poll_watchdog(uint32_t now) {
  if (static_cast<uint32_t>(now - last_command_ms_) >= WATCHDOG_MS) {
    watchdog_expired_ = true;
    stop();
  }
}

void Controller::control_wheel(unsigned index, float full_duty_pps, float dt,
                               uint32_t now, uint32_t tick_delta) {
  Wheel& wheel = wheels_[index];
  if (wheel.request <= 0) return;
  const float ceiling = mode_ == BENCH
      ? static_cast<float>(BENCH_LIMIT_PERMILLE)
      : static_cast<float>(config_.maximum_permille);
  const float limit = ceiling < config_.maximum_permille
      ? ceiling : static_cast<float>(config_.maximum_permille);
  float wanted = wheel.request;
  if (mode_ == VELOCITY) {
    const float error = wheel.request - wheel.measured_pps;
    const float feedforward = (wheel.request / full_duty_pps) * 1000.0f;
    const float candidate = clamp_value(
        wheel.integral + config_.ki * error * dt, -limit, limit);
    const float tentative = feedforward + config_.kp * error + candidate;
    // Conditional integration avoids accumulating error against saturation.
    if ((tentative >= 0 && tentative <= limit) ||
        (tentative > limit && error < 0) ||
        (tentative < 0 && error > 0)) wheel.integral = candidate;
    wanted = feedforward + config_.kp * error + wheel.integral;
  }
  wanted = clamp_value(wanted, 0, limit);
  const float step = config_.ramp_permille_per_second * dt;
  wheel.duty = clamp_value(wanted, wheel.duty - step, wheel.duty + step);
  wheel.duty = clamp_value(wheel.duty, 0, limit);
  if (wheel.duty >= 0.5f) {
    if (!wheel.stall_armed || tick_delta != 0) {
      wheel.last_fg_ms = now;
      wheel.stall_armed = true;
    } else if (static_cast<uint32_t>(now - wheel.last_fg_ms) >=
               config_.stall_timeout_ms) {
      stall_latched_ = true;
    }
  } else {
    wheel.stall_armed = false;
  }
}

void Controller::update(uint32_t now, uint32_t left_ticks,
                         uint32_t right_ticks) {
  poll_watchdog(now);
  const uint32_t elapsed = static_cast<uint32_t>(now - last_sample_ms_);
  if (elapsed == 0) return;
  last_sample_ms_ = now;
  const uint32_t ticks[2] = {left_ticks, right_ticks};
  uint32_t deltas[2];
  const float dt = static_cast<float>(elapsed) / 1000.0f;
  for (unsigned i = 0; i < 2; ++i) {
    deltas[i] = static_cast<uint32_t>(ticks[i] - wheels_[i].previous_ticks);
    wheels_[i].previous_ticks = ticks[i];
    wheels_[i].measured_pps = static_cast<float>(deltas[i]) / dt;
  }
  if (mode_ != STOPPED) {
    control_wheel(0, config_.left_pps_at_full_duty, dt, now, deltas[0]);
    control_wheel(1, config_.right_pps_at_full_duty, dt, now, deltas[1]);
    if (stall_latched_) stop();
  }
}

uint16_t Controller::left_permille() const {
  return static_cast<uint16_t>(wheels_[0].duty + 0.5f);
}

uint16_t Controller::right_permille() const {
  return static_cast<uint16_t>(wheels_[1].duty + 0.5f);
}

}  // namespace maxcim
