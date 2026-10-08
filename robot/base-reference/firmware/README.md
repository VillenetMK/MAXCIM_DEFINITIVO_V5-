# Firmware físico para Arduino Nano clásico

`maxcim_base_nano/maxcim_base_nano.ino` está destinado al **Arduino Nano
clásico ATmega328P, 16 MHz, 5 V**. No es el firmware de los brazos ESP32.
Controla dos motores BLDC con controlador incorporado, señales PWM,
CW/CCW, BRAKE y salida FG. La Raspberry Pi se comunica por USB a 115200.

Los contadores salen de interrupciones sobre los **pulsos FG físicos**.
No se genera movimiento simulado, ni se convierten las órdenes PWM en
distancia recorrida. La conversión de pulsos a metros se hace en ROS 2
después de medir la calibración de las ruedas.

## Estado inicial y datos pendientes

El firmware entregado inicia frenado y con el PWM inactivo. Por defecto
`LEFT_WIRING_CONFIRMED`, `RIGHT_WIRING_CONFIRMED` y
`CONTROL_CALIBRATION_CONFIRMED` están en `false` en
`maxcim_base_nano/config.h`. **Así se puede leer FG sin autorizar motores.**

Falta verificar en el robot:

- Función de cada cable, alimentación exacta de la variante 12/24 V,
  compatibilidad de sus entradas con 5 V y significado real de BRAKE/PWM.
- Nivel CW/CCW que produce avance en cada lado, con las ruedas montadas.
- Pulsos FG por vuelta completa de cada rueda, diámetro y separación de
  ruedas para ROS 2; la cifra del motor puede referirse al eje previo a la
  reductora y no debe copiarse como pulsos de rueda.
- Respuesta real de cada motor al PWM, parámetros PI y efecto de la carga.

## Cableado propuesto para instalación nueva

Esta tabla **no describe el cableado que aparece en las fotos**. Hay que
identificarlo y adaptarlo antes de habilitar las salidas.

| Señal del controlador del motor | Rueda izquierda | Rueda derecha |
|---|---|---|
| FG, salida de pulsos | Nano D2 | Nano D3 |
| PWM, entrada activa en LOW | Nano D9 | Nano D10 |
| CW/CCW, sentido fijo de avance | Nano D4 | Nano D7 |
| BRAKE, LOW frenado / HIGH habilitado | Nano D8 | Nano D12 |
| GND de señales | GND común con Nano | GND común con Nano |
| VCC de potencia | Fuente adecuada al motor | Fuente adecuada al motor |

El Nano **no alimenta los motores**. La fuente de potencia se conecta al
controlador incorporado de cada motor. Alimentar el Nano por USB de la
Raspberry; no conectar VCC de motor a 5 V, VIN ni a ningún GPIO del Nano.
Para unir masas, comprobar antes que la alimentación y las referencias de
señal del controlador permiten esa conexión.

Si FG se confirma como colector abierto NPN según la ficha del motor,
usar un pull-up **externo de 4.7 kΩ entre cada FG y los 5 V del Nano**.
Se cuenta un flanco `FALLING` por pulso. Nunca conectar estas señales
de 5 V directamente a los GPIO de 3.3 V de la Raspberry.

Para mantener la parada durante el reset, el cargador de arranque y la
desconexión del USB, disponer de polarización externa que mantenga cada
BRAKE en LOW y PWM en HIGH cuando el Nano no conduzca sus pines,
compatible con las entradas verificadas del controlador. El software solo
puede fijar niveles una vez ejecutado `setup()`. Si el Nano se alimenta
por USB, la polarización necesaria para el controlador debe seguir siendo
válida al quitar ese USB; confirmarlo en el montaje real. No habilitar
la potencia hasta comprobar ese estado de parada.

## Compilar y cargar

1. Abrir `maxcim_base_nano/maxcim_base_nano.ino` en Arduino IDE.
2. Seleccionar **Arduino AVR Boards → Arduino Nano** y procesador
   **ATmega328P**. Para una placa con cargador antiguo, seleccionar
   **ATmega328P (Old Bootloader)**. No seleccionar Nano Every/ESP32.
3. Seleccionar el puerto USB que corresponda al Nano. Mantener la potencia
   de los motores desconectada al cargar firmware; abrir el puerto serie
   suele reiniciar esta placa.
4. Compilar y cargar. No hacen falta bibliotecas Arduino externas.

Con Arduino CLI y el paquete `arduino:avr` ya instalado:

```bash
arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328 firmware/maxcim_base_nano
```

Para cargar, seleccionar el puerto real del Nano en el comando de carga
del CLI o en el IDE. La Raspberry puede tener otros dispositivos serie
conectados: **no elegir `/dev/ttyUSB0` solo por ser el primero**.

## Lectura de FG y puesta en marcha

El protocolo completo está en `../docs/PROTOCOL.md`. Todas las líneas
incluyen CRC16 y terminan únicamente con `\n`. El monitor serie no sirve
para enviar comandos sin calcular el CRC; usar las herramientas del
repositorio. La identidad debe ser `MAXCIM_BASE`, versión `1`, antes de
aceptar este puerto como controlador de ruedas.

Con la potencia desconectada, se puede comprobar si girar una rueda
manualmente produce pulsos FG. Algunos controladores necesitan su propia
alimentación para emitir FG; por eso **la ausencia de pulsos sin potencia
no demuestra una avería**. En ese caso la comprobación requiere la
alimentación correcta y un montaje que mantenga los motores parados.

Después de verificar el cableado, establecer los dos permisos de cableado
en `true`, ajustar los dos niveles de avance y volver a cargar. Entonces
queda disponible `D`, la prueba de banco limitada a **150 permille = 15 %**
como máximo, con rampa. Hacerla con las ruedas libres y el robot sujeto.
La prueba se frena si no recibe un comando válido en **350 ms** o si una
rueda activada no produce FG durante **2 segundos**. Si a ese PWM el motor
no vence su zona muerta, también se declarará fallo: no demuestra por sí
solo un atasco ni permite concluir que el cableado FG esté mal. Revisar el
montaje y la respuesta antes de ajustar límites.

La telemetría informa `left_pps`, `right_pps` y duty **aplicado**, no solo
la consigna. Para obtener una escala inicial del feedforward:

```text
LEFT_PPS_AT_FULL_DUTY  = PPS izquierdos observados / (PWM izquierdo aplicado / 1000)
RIGHT_PPS_AT_FULL_DUTY = PPS derechos observados / (PWM derecho aplicado / 1000)
```

El nombre expresa la escala normalizada del modelo, **no una velocidad
máxima medida ni una instrucción para probar 100 % de PWM**. Es una
aproximación inicial a partir de mediciones de PWM bajo; la zona muerta y
la carga pueden hacerla inexacta. Comprobar varias mediciones estables y
ajustar el PI en el banco. Dejar ambos valores en cero mientras sean
desconocidos. Solo entonces habilitar
`CONTROL_CALIBRATION_CONFIRMED` y cargar de nuevo para permitir `V`.

El máximo inicial de `V` es **300 permille = 30 %**, con rampa de
300 permille/s. `KP=1.0` y `KI=0.5` son valores iniciales conservadores
**sin validación física para estos motores**, no una calibración terminada.
El PI combina feedforward, velocidad medida mediante FG e integral con
antiwindup. La consigna cero frena esa rueda inmediatamente, aunque la
otra siga avanzando.

## Paradas, giro y límites

- Ambas ruedas solo avanzan en su sentido físico confirmado. Para girar,
  ROS 2 pide velocidades distintas. Un giro puede frenar una rueda y
  hacer avanzar la otra. Este firmware no ofrece marcha atrás ni giro
  sobre el centro con ruedas en sentidos opuestos.
- Los niveles de CW/CCW se establecen una sola vez al arrancar con freno
  activado; ningún comando de red cambia el sentido durante la marcha.
- `S`, `H`, una trama inválida y el watchdog detienen inmediatamente. Una
  consigna cero detiene inmediatamente la rueda correspondiente.
- Una orden repetida, atrasada, de otra sesión o con CRC inválido no
  renueva el watchdog. La siguiente secuencia debe ser mayor que la
  última aceptada; después de `UINT32_MAX` se requiere nueva sesión `H`.
- El fallo FG/atasco se enclava y bloquea ambos motores. `H` y `S` no lo
  borran. Revisar el hardware, dejarlo parado y reiniciar físicamente el
  Nano para rearmarlo. Reconectar USB también puede reiniciar la placa.
- Timer1 genera 25 kHz en D9/D10. No añadir `Servo`, `analogWrite` en esos
  pines ni otra biblioteca que reconfigure Timer1. Timer0 sigue dando
  `millis()`. Los tiempos y contadores soportan su desbordamiento uint32.
- El envío serie usa un buffer acotado y nunca espera indefinidamente
  para transmitir. La lectura serie procesa como máximo 64 bytes por
  vuelta para que el watchdog siga ejecutándose frente a datos basura.

Un FG de un canal no conoce el sentido de un empuje externo ni detecta
deslizamiento. La odometría exige el supuesto físico de avance que usa
este proyecto; pulsos reales no equivalen a posición absoluta exacta.

## Verificación realizada sin motores

Desde la raíz del repositorio:

```bash
bash tests/firmware/run.sh
```

Las pruebas nativas ejecutan el mismo `control_core.cpp` del Nano y
comprueban CRC, parser y límites de trama, sesiones/secuencias, bloqueo
por cableado/calibración, watchdog, parada independiente, velocidad FG,
PI/rampa/saturación, fallo de una sola rueda, enclavamiento y
desbordamientos. La compilación AVR comprueba el sketch y el núcleo
Arduino. **Estas verificaciones no sustituyen las pruebas físicas del
cableado, los niveles eléctricos, la frenada ni la sintonía del PI.**
