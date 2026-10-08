#ifndef MAXCIM_CONTROL_CORE_H
#define MAXCIM_CONTROL_CORE_H

#include <stddef.h>
#include <stdint.h>

namespace maxcim {

static const size_t MAX_LINE_BYTES = 180;
static const uint16_t WATCHDOG_MS = 350;
static const uint16_t BENCH_LIMIT_PERMILLE = 150;

enum StatusFlag {
  WIRING_PENDING = 1,
  WATCHDOG_EXPIRED = 2,
  STALL_LATCHED = 4,
  CALIBRATION_PENDING = 8
};

uint16_t crc16(const char* data, size_t length);

struct Command {
  char type;
  uint32_t session;
  uint32_t sequence;
  float left;
  float right;
};

// Parse in place, after checking CRC; no allocation or Arduino dependency.
bool parse_frame(char* line, Command& command);

class LineReader {
 public:
  enum Result { PENDING, READY, INVALID };
  LineReader();
  Result feed(char value);
  char* line() { return data_; }

 private:
  char data_[MAX_LINE_BYTES];
  size_t used_;
  bool discarding_;
};

struct Config {
  bool wiring_confirmed;
  bool calibration_confirmed;
  float left_pps_at_full_duty;
  float right_pps_at_full_duty;
  float kp;
  float ki;
  uint16_t maximum_permille;
  float ramp_permille_per_second;
  uint16_t stall_timeout_ms;
};

class Controller {
 public:
  enum Mode { STOPPED, VELOCITY, BENCH };
  enum Result { REJECTED, ACCEPTED, HELLO };

  explicit Controller(const Config& config);
  void begin(uint32_t now, uint32_t left_ticks, uint32_t right_ticks);
  Result handle(const Command& command, uint32_t now);
  void invalid_frame();
  void poll_watchdog(uint32_t now);
  void update(uint32_t now, uint32_t left_ticks, uint32_t right_ticks);
  void stop();

  uint8_t flags() const;
  uint32_t session() const { return session_; }
  uint32_t ack_sequence() const { return sequence_; }
  uint16_t left_permille() const;
  uint16_t right_permille() const;
  float left_pps() const { return wheels_[0].measured_pps; }
  float right_pps() const { return wheels_[1].measured_pps; }
  Mode mode() const { return mode_; }

 private:
  struct Wheel {
    float request;
    float duty;
    float integral;
    float measured_pps;
    uint32_t previous_ticks;
    uint32_t last_fg_ms;
    bool stall_armed;
  };

  Config config_;
  Wheel wheels_[2];
  Mode mode_;
  uint32_t session_;
  uint32_t sequence_;
  uint32_t last_command_ms_;
  uint32_t last_sample_ms_;
  bool watchdog_expired_;
  bool stall_latched_;

  bool calibrated() const;
  void set_request(unsigned index, float request, uint32_t now);
  void control_wheel(unsigned index, float full_duty_pps, float dt,
                     uint32_t now, uint32_t tick_delta);
};

}  // namespace maxcim
#endif
