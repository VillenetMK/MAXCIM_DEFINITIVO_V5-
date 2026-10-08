# Hardware y conexiones

Fuente principal: [MAXCIM-CONEXIONES.drawio](hardware/MAXCIM-CONEXIONES.drawio), archivo proporcionado por Gabriel. Se conserva sin modificación. Las conexiones de ese esquema no sustituyen una comprobación eléctrica en el robot.

| Componente | Conexión indicada | Papel |
|---|---|---|
| Jetson | Ethernet al router; USB a cámara; HDMI a pantalla | Visión, conversación y pantalla |
| Raspberry Pi | Ethernet al router; USB a Nano, ESP32 y LiDAR | Control ROS 2 y periféricos físicos |
| Arduino Nano | PWM, dirección y encoders de dos motores | Base y feedback de ruedas |
| ESP32 | I2C hacia PCA9685 | Seis canales de servos: 0,1,2,4,5,6 |
| PCA9685 | Alimentación de servos indicada desde regulador 5.5 V | PWM de brazos |
| Batería 24–28 V | Interruptor y regulación indicada de 24 V | Dos motores de base |
| Batería 12 V | Jetson/router y regulación indicada de 5 V | Computadoras y comunicaciones |
| Micrófono/altavoz | Referencias a audio en el diagrama | Ruta y dispositivos ALSA pendientes de confirmar |

El diagrama etiqueta dos líneas I2C como `SCL/CLK`; debe comprobarse cuál es SDA y cuál SCL. El firmware recuperado usa SDA21/SCL22 en ESP32. No alimentar lógica de ESP32/Pi a 5.5 V por interpretar esa línea como alimentación común: verificar placa, regulador y conexión real antes de energizar.

## Incompatibilidades detectadas

1. El firmware anterior transforma 0..270 en pulsos 100..520; esto es una **escala lógica**, no prueba de recorrido mecánico de 270°.
2. Los límites de canales 1,5,6 y algunos valores home se contradicen entre comentarios de firmware y configuración Python.
3. El firmware anterior hace homing al arrancar y no tiene un STOP que cancele el movimiento actual. V5 ofrece un firmware separado con arranque sin PWM, INIT/ENABLE explícitos y STOP que mantiene la posición mandada.
4. El controlador viejo de la base manda CSV, mientras el Nano más reciente usa sesión, CRC y telemetría FG. La ruta V5 utiliza este último protocolo; no mezclarlo con el firmware CSV.
5. El firmware Nano nuevo propone pines y polaridades para una instalación comprobada. No se ha determinado que coincidan con el cableado existente. Consulta [conexiones](../robot/base-reference/docs/CONEXIONES.md) y [firmware](../robot/base-reference/firmware/README.md).

## Datos por medir

- Puertos estables `/dev/serial/by-id/`, modelo real de LiDAR y baudrate. 115200 es el valor del perfil recuperado, no detección automática.
- Radios cargados de ambas ruedas, separación, pulsos FG por vuelta, sentido físico y respuesta PI. El perfil V5 mantiene ceros y flags de calibración en falso hasta medirlos.
- Pulsos mínimos/máximos y home de cada servo, sin rozamiento ni colisión; colocar manualmente en home antes de INIT/ENABLE. La configuración lógica heredada no valida el recorrido real.
- Posición/orientación de LiDAR y cámara; geometría y velocidad de frenado. El URDF viejo declara una base 0.60×0.50 y offsets que deben contrastarse con el robot.
- Modelo/SO/JetPack de Jetson, SO de Raspberry y distribución ROS instalada en cada una.

La parada del panel es una función de software. La parada física debe cortar o inhibir la energía de los motores por un circuito adecuado; los servos pueden dejar caer peso al quitar PWM. El firmware V5 mantiene último PWM al vencer el watchdog, sin sensor de posición ni confirmación mecánica.
