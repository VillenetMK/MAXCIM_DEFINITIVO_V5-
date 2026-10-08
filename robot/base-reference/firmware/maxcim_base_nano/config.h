#ifndef MAXCIM_NANO_CONFIG_H
#define MAXCIM_NANO_CONFIG_H

#include "control_core.h"

namespace hardware {

// Propuesta para instalación NUEVA: verificar cada cable antes de cambiar
// estos permisos. Los colores del cable no identifican su función.
constexpr bool LEFT_WIRING_CONFIRMED = false;
constexpr bool RIGHT_WIRING_CONFIRMED = false;
constexpr bool CONTROL_CALIBRATION_CONFIRMED = false;

constexpr uint8_t LEFT_FG_PIN = 2;
constexpr uint8_t RIGHT_FG_PIN = 3;
constexpr uint8_t LEFT_PWM_PIN = 9;   // OC1A: fijo para Timer1.
constexpr uint8_t RIGHT_PWM_PIN = 10; // OC1B: fijo para Timer1.
constexpr uint8_t LEFT_DIRECTION_PIN = 4;
constexpr uint8_t RIGHT_DIRECTION_PIN = 7;
constexpr uint8_t LEFT_BRAKE_PIN = 8;
constexpr uint8_t RIGHT_BRAKE_PIN = 12;

// Pendiente comprobar qué nivel produce AVANCE de cada rueda en su montaje.
// Estos niveles se aplican una única vez, al arrancar con freno activado.
// No hay inversión durante la ejecución ni comandos de marcha atrás.
constexpr bool LEFT_FORWARD_LEVEL_HIGH = false;
constexpr bool RIGHT_FORWARD_LEVEL_HIGH = false;

// Escala inicial del feedforward: PPS observados / (duty_permille / 1000).
// Se estima por separado a partir de una prueba D de PWM bajo y FG real.
// NO exige probar 100% de PWM ni afirma velocidad máxima medida: es una
// aproximación lineal local, por validar con carga. CERO = desconocido.
// Unidades: flancos FALLING FG/s por fracción PWM, no RPM ni pulsos/vuelta.
constexpr float LEFT_PPS_AT_FULL_DUTY = 0.0f;
constexpr float RIGHT_PPS_AT_FULL_DUTY = 0.0f;

// Punto inicial conservador, NO ajuste validado para estos motores/carga.
// Kp: permille/(pulso/s). Ki: permille/(pulso/s)/s.
constexpr float KP = 1.0f;
constexpr float KI = 0.5f;
constexpr uint16_t MAXIMUM_PERMILLE = 300; // Techo inicial V de 30%, ajustable.
constexpr float RAMP_PERMILLE_PER_SECOND = 300.0f;
constexpr uint16_t STALL_TIMEOUT_MS = 2000;
constexpr uint16_t SAMPLE_PERIOD_MS = 50;

static_assert(MAXIMUM_PERMILLE > 0 && MAXIMUM_PERMILLE <= 1000,
              "PWM debe estar entre 1 y 1000 permille");
static_assert(RAMP_PERMILLE_PER_SECOND > 0,
              "La rampa debe ser positiva");
static_assert(STALL_TIMEOUT_MS >= 500 && STALL_TIMEOUT_MS <= 5000,
              "Revisar tiempo de atasco: entre 500 y 5000 ms");
static_assert(!CONTROL_CALIBRATION_CONFIRMED ||
              (LEFT_PPS_AT_FULL_DUTY > 0 && RIGHT_PPS_AT_FULL_DUTY > 0),
              "No habilitar V sin caracterizar ambos motores");

const maxcim::Config CONTROL = {
  LEFT_WIRING_CONFIRMED && RIGHT_WIRING_CONFIRMED,
  CONTROL_CALIBRATION_CONFIRMED,
  LEFT_PPS_AT_FULL_DUTY,
  RIGHT_PPS_AT_FULL_DUTY,
  KP,
  KI,
  MAXIMUM_PERMILLE,
  RAMP_PERMILLE_PER_SECOND,
  STALL_TIMEOUT_MS
};

}  // namespace hardware
#endif
