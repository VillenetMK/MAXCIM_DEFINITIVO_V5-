# Validación realizada · 7 de septiembre de 2026

## Comprobado en el entorno de desarrollo

- **85 pruebas Python aprobadas**: protocolo/CLI, cinemática, callbacks del
  puente, habilitación/caducidad y odometría. Ejecutar `bash scripts/check.sh`.
- **Núcleo C++ del Nano aprobado**: CRC, límites de líneas, bloqueo de salidas,
  sesiones/secuencias, watchdog, parada individual, FG, PI, rampa, saturación,
  fallo por falta de FG y desbordamiento uint32.
- **Compilación AVR real aprobada** con Arduino AVR GCC 7.3.0 y core Arduino
  AVR 1.8.6 para ATmega328P a 16 MHz. Programa: 13 440 bytes; variables
  globales: 783 de los 2048 bytes de SRAM. No se cargó en ninguna placa.
- Sintaxis Python y manifiestos XML verificados.
- Emulador de puerto PTY comprobado con identificación CRC, confirmación de
  mandos y pulsos inyectados explícitamente.

## Preparado, pendiente de ejecutar

`tests/ros_smoke.py` usa ROS 2 real y un Nano emulado en un pseudo-terminal.
Comprueba el recorrido completo desde USB a mensajes/TF, así como la parada
por pérdida de órdenes o telemetría. El entorno de desarrollo no tiene ROS
instalado; **no se afirma que esta integración haya pasado**. El workflow
configura su ejecución en ROS 2 Humble y Jazzy al publicar el repositorio.

## Pendiente en el robot físico

Pinout real, tensión nominal, sentido permitido y estados de entrada durante
encendido/reset. FG y pulsos por vuelta de cada rueda. Radios y separación.
Respuesta PWM/velocidad, ajuste PI, arranque con carga, detención, frenado,
curvas, desconexión USB, precisión de distancia/ángulo y deslizamiento.

Ni las pruebas matemáticas ni compilar firmware demuestran por sí solas que
el robot esté calibrado o pueda navegar de forma autónoma.

## Separación Linux

Este repositorio conserva los tres paquetes ROS 2, el firmware y las pruebas
del puente y la odometría. El panel y las pruebas Windows se mantienen en su
repositorio independiente. El firmware se copió sin cambios respecto al
compilado para AVR indicado arriba.
