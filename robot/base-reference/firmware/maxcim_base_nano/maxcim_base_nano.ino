// MAXCIM: Nano clásico ATmega328P, dos controladores BLDC con FG independiente.
// Nunca conectar la alimentación de motor al Nano ni el motor directamente
// a un GPIO. Consultar firmware/README.md antes de cablear o habilitar.
#include <Arduino.h>
#include <util/atomic.h>
#include "config.h"
#include "control_core.h"

#if !defined(__AVR_ATmega328P__)
#error "Este firmware requiere Arduino Nano clasico ATmega328P"
#endif
#if F_CPU != 16000000UL
#error "Timer1 esta calculado para un ATmega328P a 16 MHz"
#endif

volatile uint32_t left_fg_ticks = 0;
volatile uint32_t right_fg_ticks = 0;
maxcim::Controller controller(hardware::CONTROL);
maxcim::LineReader receiver;
uint32_t telemetry_sequence = 0;
uint32_t last_sample_ms = 0;
bool identity_pending = false;

// Una sola trama pendiente. Si UART se retrasa se omiten muestras nuevas;
// jamás se bloquea el control esperando espacio en el buffer de transmisión.
char outgoing[maxcim::MAX_LINE_BYTES];
size_t outgoing_size = 0;
size_t outgoing_position = 0;

void left_fg_interrupt() { ++left_fg_ticks; }
void right_fg_interrupt() { ++right_fg_ticks; }

void read_ticks(uint32_t& left, uint32_t& right) {
  ATOMIC_BLOCK(ATOMIC_RESTORESTATE) {
    left = left_fg_ticks;
    right = right_fg_ticks;
  }
}

void set_left_duty(uint16_t permille) {
  if (permille == 0) {
    digitalWrite(hardware::LEFT_BRAKE_PIN, LOW);
    TCCR1A &= ~(_BV(COM1A1) | _BV(COM1A0));
    digitalWrite(hardware::LEFT_PWM_PIN, HIGH);
    return;
  }
  // Fast PWM modo 14, salida invertida: LOW = fracción activa.
  OCR1A = static_cast<uint16_t>((640UL * permille) / 1000UL);
  TCCR1A |= _BV(COM1A1) | _BV(COM1A0);
  digitalWrite(hardware::LEFT_BRAKE_PIN, HIGH);
}

void set_right_duty(uint16_t permille) {
  if (permille == 0) {
    digitalWrite(hardware::RIGHT_BRAKE_PIN, LOW);
    TCCR1A &= ~(_BV(COM1B1) | _BV(COM1B0));
    digitalWrite(hardware::RIGHT_PWM_PIN, HIGH);
    return;
  }
  OCR1B = static_cast<uint16_t>((640UL * permille) / 1000UL);
  TCCR1A |= _BV(COM1B1) | _BV(COM1B0);
  digitalWrite(hardware::RIGHT_BRAKE_PIN, HIGH);
}

void apply_outputs() {
  set_left_duty(controller.left_permille());
  set_right_duty(controller.right_permille());
}

void finish_frame(int payload_size) {
  if (payload_size <= 0 || static_cast<size_t>(payload_size) + 6 >=
                           sizeof(outgoing)) {
    outgoing_size = outgoing_position = 0;
    return;
  }
  const uint16_t checksum = maxcim::crc16(outgoing, payload_size);
  const int suffix_size = snprintf(outgoing + payload_size,
      sizeof(outgoing) - payload_size, "*%04X\n", checksum);
  outgoing_size = static_cast<size_t>(payload_size + suffix_size);
  outgoing_position = 0;
}

void queue_identity() {
  const int count = snprintf(outgoing, sizeof(outgoing),
      "I %lu MAXCIM_BASE 1 %u", static_cast<unsigned long>(controller.session()),
      static_cast<unsigned>(controller.flags()));
  finish_frame(count);
}

void queue_telemetry(uint32_t now, uint32_t left, uint32_t right) {
  char left_pps[24], right_pps[24];
  dtostrf(controller.left_pps(), 1, 2, left_pps);
  dtostrf(controller.right_pps(), 1, 2, right_pps);
  const int count = snprintf(outgoing, sizeof(outgoing),
      "T %lu %lu %lu %lu %lu %s %s %u %u %u %lu",
      static_cast<unsigned long>(controller.session()),
      static_cast<unsigned long>(++telemetry_sequence),
      static_cast<unsigned long>(now), static_cast<unsigned long>(left),
      static_cast<unsigned long>(right), left_pps, right_pps,
      static_cast<unsigned>(controller.left_permille()),
      static_cast<unsigned>(controller.right_permille()),
      static_cast<unsigned>(controller.flags()),
      static_cast<unsigned long>(controller.ack_sequence()));
  finish_frame(count);
}

void flush_output() {
  if (outgoing_position >= outgoing_size) return;
  const int available = Serial.availableForWrite();
  if (available <= 0) return;
  const size_t remaining = outgoing_size - outgoing_position;
  const size_t amount = remaining < static_cast<size_t>(available)
      ? remaining : static_cast<size_t>(available);
  outgoing_position += Serial.write(
      reinterpret_cast<const uint8_t*>(outgoing + outgoing_position), amount);
}

void setup() {
  // Latch HIGH before OUTPUT prevents a low PWM pulse during initialization.
  digitalWrite(hardware::LEFT_PWM_PIN, HIGH);
  digitalWrite(hardware::RIGHT_PWM_PIN, HIGH);
  digitalWrite(hardware::LEFT_BRAKE_PIN, LOW);
  digitalWrite(hardware::RIGHT_BRAKE_PIN, LOW);
  pinMode(hardware::LEFT_PWM_PIN, OUTPUT);
  pinMode(hardware::RIGHT_PWM_PIN, OUTPUT);
  pinMode(hardware::LEFT_BRAKE_PIN, OUTPUT);
  pinMode(hardware::RIGHT_BRAKE_PIN, OUTPUT);
  digitalWrite(hardware::LEFT_DIRECTION_PIN,
               hardware::LEFT_FORWARD_LEVEL_HIGH ? HIGH : LOW);
  digitalWrite(hardware::RIGHT_DIRECTION_PIN,
               hardware::RIGHT_FORWARD_LEVEL_HIGH ? HIGH : LOW);
  pinMode(hardware::LEFT_DIRECTION_PIN, OUTPUT);
  pinMode(hardware::RIGHT_DIRECTION_PIN, OUTPUT);

  // 16 MHz / (1 * (639 + 1)) = 25 kHz. Timer0 conserva millis().
  TCCR1A = _BV(WGM11);
  TCCR1B = 0;
  TCNT1 = 0;
  ICR1 = 639;
  OCR1A = 0;
  OCR1B = 0;
  TCCR1B = _BV(WGM13) | _BV(WGM12) | _BV(CS10);

  // FG de colector abierto requiere pull-up EXTERNO 4.7k a 5 V verificado.
  pinMode(hardware::LEFT_FG_PIN, INPUT);
  pinMode(hardware::RIGHT_FG_PIN, INPUT);
  attachInterrupt(digitalPinToInterrupt(hardware::LEFT_FG_PIN),
                  left_fg_interrupt, FALLING);
  attachInterrupt(digitalPinToInterrupt(hardware::RIGHT_FG_PIN),
                  right_fg_interrupt, FALLING);
  Serial.begin(115200);
  const uint32_t now = millis();
  uint32_t left, right;
  read_ticks(left, right);
  controller.begin(now, left, right);
  last_sample_ms = now;
  apply_outputs();
}

void loop() {
  controller.poll_watchdog(millis());
  apply_outputs();
  // Bound RX work so a stream of junk cannot starve the local watchdog.
  for (uint8_t budget = 0; budget < 64 && Serial.available() > 0; ++budget) {
    const maxcim::LineReader::Result result =
        receiver.feed(static_cast<char>(Serial.read()));
    if (result == maxcim::LineReader::INVALID) {
      controller.invalid_frame();
      apply_outputs();
    } else if (result == maxcim::LineReader::READY) {
      maxcim::Command command;
      if (!maxcim::parse_frame(receiver.line(), command)) {
        controller.invalid_frame();
      } else if (controller.handle(command, millis()) ==
                 maxcim::Controller::HELLO) {
        telemetry_sequence = 0;
        identity_pending = true;
      }
      apply_outputs();
    }
  }
  const uint32_t now = millis();
  controller.poll_watchdog(now);
  apply_outputs();
  uint32_t left = 0, right = 0;
  const bool sample_due = static_cast<uint32_t>(now - last_sample_ms) >=
                          hardware::SAMPLE_PERIOD_MS;
  if (sample_due) {
    read_ticks(left, right);
    controller.update(now, left, right);
    last_sample_ms = now;
    apply_outputs();
  }
  flush_output();
  if (outgoing_position >= outgoing_size) {
    if (identity_pending) {
      queue_identity();
      identity_pending = false;
    } else if (sample_due && controller.session() != 0) {
      queue_telemetry(now, left, right);
    }
  }
  flush_output();
}
