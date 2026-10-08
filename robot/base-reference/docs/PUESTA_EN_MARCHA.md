# Puesta en marcha desde cero

Primero se comprueba el Nano y cada motor; después se habilita ROS. No hay
necesidad de cambiar los repositorios de brazos, visión o sensores.

## 1. Comprobar la Raspberry

En la terminal de la Raspberry:

```bash
cat /etc/os-release
uname -m
ls /opt/ros
ls -l /dev/serial/by-id/
```

Ubuntu 22.04 usa ROS 2 Humble y Ubuntu 24.04 usa ROS 2 Jazzy. Si ROS no está
instalado, seguir la guía oficial correspondiente: [Humble](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html)
o [Jazzy](https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html).
Estas instrucciones de paquetes no son para Raspberry Pi OS/Debian ni Ubuntu
de otra versión. Si ya hay ROS, usar esa instalación sin reinstalarla.

## 2. Descargar el repositorio independiente

Una vez publicado `VillenetMK/maxcim-base-linux`:

```bash
cd ~
git clone https://github.com/VillenetMK/maxcim-base-linux.git
cd ~/maxcim-base-linux
sudo apt update
sudo apt install python3-serial python3-colcon-common-extensions python3-rosdep
```

Si el directorio ya existe, entrar y comprobar `git status` antes de actualizar
con `git pull --ff-only`; no clonarlo dentro de sí mismo ni borrar cambios.
No se requiere comprimir el proyecto.

## 3. Cablear y cargar primero el Nano bloqueado

Seguir [CONEXIONES.md](CONEXIONES.md). Dejar inicialmente desconectada la
alimentación de los motores. Abrir Arduino IDE y el archivo:

```text
firmware/maxcim_base_nano/maxcim_base_nano.ino
```

Instalar el paquete de placas **Arduino AVR Boards**, seleccionar **Arduino
Nano**, procesador **ATmega328P**, y el puerto concreto del Nano. Si la placa
tiene bootloader antiguo y falla la carga, probar la selección
**ATmega328P (Old Bootloader)**; eso no requiere modificar el programa.
No elegir Nano Every, Nano ESP32 o Nano 33: son placas distintas.

Cargar con los indicadores `LEFT_WIRING_CONFIRMED`, `RIGHT_WIRING_CONFIRMED`
y `CONTROL_CALIBRATION_CONFIRMED` en `false`, como vienen. Cerrar después el
monitor serie de Arduino para liberar el puerto.

Quien ya use Arduino CLI puede compilar y cargar así, sustituyendo PUERTO:

```bash
arduino-cli core install arduino:avr@1.8.6
arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328 firmware/maxcim_base_nano
arduino-cli upload --port PUERTO --fqbn arduino:avr:nano:cpu=atmega328 firmware/maxcim_base_nano
```

## 4. Comprobar USB e identidad sin movimiento

Guardar la ruta REAL obtenida con `ls -l /dev/serial/by-id/`:

```bash
export NANO_PORT='/dev/serial/by-id/REEMPLAZAR_POR_LA_RUTA_REAL'
python3 scripts/nano_tool.py --port "$NANO_PORT" info
```

Debe aparecer `MAXCIM_BASE v1`, contadores FG y PWM cero. `flags=9` significa
cableado y calibración pendientes: es normal con la configuración inicial.
Si aparece «Permission denied», añadir al usuario al grupo del puerto
(normalmente `dialout`) y cerrar/abrir sesión:

```bash
sudo usermod -aG dialout "$USER"
```

No continuar con un puerto distinto por ensayo: verificar que sea el Nano.

## 5. Verificar una rueda cada vez

Comprobar primero tensión nominal, señales, resistencias y sentido permitido
según CONEXIONES.md. Dejar ambas ruedas motrices elevadas y el chasis sujeto.
Configurar los niveles de avance fijo en `firmware/maxcim_base_nano/config.h`.
Solo tras revisar el cableado, cambiar los dos indicadores de cableado a
`true`, mantener `CONTROL_CALIBRATION_CONFIRMED=false`, y volver a cargar.

Conectar alimentación de motores y ejecutar una prueba breve:

```bash
python3 scripts/nano_tool.py --port "$NANO_PORT" bench --wheel left --duty 0.08 --seconds 1 --wiring-confirmed
python3 scripts/nano_tool.py --port "$NANO_PORT" bench --wheel right --duty 0.08 --seconds 1 --wiring-confirmed
```

Cada prueba debe mover solo la rueda indicada, en el sentido de avance del
chasis, y aumentar solo su contador FG. La otra rueda queda frenada. Un PWM
del 8% es una prueba inicial; no garantiza que el motor supere su fricción.
La herramienta limita PWM al 15% y duración a 2 s. Si no gira o gira sin FG,
detener y revisar alimentación, freno, PWM, pull-up y señal; no habilitar PI
para compensar a ciegas. Se enclava fallo FG si mantiene mando sin pulsos
durante el umbral configurado de 2 s; H no borra ese fallo.

## 6. Calibrar los datos reales

| Dato | Cómo obtenerlo | Dónde escribirlo |
|---|---|---|
| Radio izquierdo/derecho | Diámetro efectivo de rueda /2, en metros | `left_wheel_radius_m`, `right_wheel_radius_m` |
| Separación | Distancia lateral entre centros de ruedas motrices, metros | `wheel_separation_m` |
| FG por vuelta de rueda | Incremento FG / vueltas físicas observadas | `left_pulses_per_revolution`, `right_pulses_per_revolution` |
| Escala inicial de velocidad | PPS estables / fracción PWM real mostrada | `LEFT_PPS_AT_FULL_DUTY`, `RIGHT_PPS_AT_FULL_DUTY` del Nano |

Marcar una referencia visible en la rueda y contar vueltas completas o una
fracción conocida durante las pruebas (puede ayudar grabar vídeo). Repetir
para reducir el error. No forzar manualmente una reductora que no se deje
mover. Si el ensayo breve no permite medir vueltas con precisión, usar la
relación de reducción documentada y verificarla después físicamente.

La escala `PPS_AT_FULL_DUTY` es una aproximación inicial de la relación entre
PWM y PPS calculada a partir de una prueba de bajo PWM; **no exige ensayar
100% de potencia** ni confirma la velocidad máxima. Usar lecturas estables,
no el promedio que incluye la aceleración y frenada. `KP`/`KI` son ajustes
iniciales que hay que afinar bajo carga. Primero verificar FG, luego ajustar
el control; PWM no es proporcional a velocidad en todas las condiciones.

Tras introducir ambas escalas positivas y comprobar que FG funciona,
poner `CONTROL_CALIBRATION_CONFIRMED=true` y cargar el Nano. El límite PWM
inicial de V es 30%, configurable; los límites ROS en m/s no garantizan que
ese PWM alcance la velocidad solicitada.

## 7. Construir los paquetes ROS 2

Elegir **solo** la línea de entorno que corresponda al sistema:

```bash
source /opt/ros/humble/setup.bash
# En Ubuntu 24.04 con Jazzy, usar en su lugar:
# source /opt/ros/jazzy/setup.bash
cd ~/maxcim-base-linux
```

Si `rosdep` nunca se inicializó, ejecutar `sudo rosdep init` una sola vez.
Después:

```bash
rosdep update
rosdep install --from-paths src --ignore-src -r -y
colcon build --symlink-install
source install/setup.bash
cp src/maxcim_base/config/hardware.yaml hardware.local.yaml
nano hardware.local.yaml
```

En `hardware.local.yaml`, colocar `serial_port`, los cinco datos medidos de
geometría/FG, `forward_direction_confirmed: true` y
`calibration_confirmed: true`. Mantener `use_sim_time: false`.
El archivo local no se sube a Git; conservar sus valores como calibración del
robot. Cambiar parámetros exige reiniciar los nodos; no hay ajuste en caliente.

## 8. Arrancar puente y odometría

Terminal 1:

```bash
cd ~/maxcim-base-linux
source install/setup.bash
ros2 launch maxcim_base base.launch.py config:="$HOME/maxcim-base-linux/hardware.local.yaml" odometry:=true
```

La base sigue parada hasta habilitarla. Para comprobar solo el puerto antes
de calibrar, usar `odometry:=false`, con los indicadores de calibración falsos;
el puente puede mostrar telemetría pero no permite movimiento por `/cmd_vel`.

Terminal 2, cargar el mismo entorno y comprobar:

```bash
cd ~/maxcim-base-linux
source install/setup.bash
ros2 topic echo /nano_base/status
```

Interrumpir el `echo` con Ctrl+C. Habilitar y enviar avance breve:

```bash
ros2 service call /nano_base/enable std_srvs/srv/SetBool '{data: true}'
timeout --signal=INT 2s ros2 topic pub --rate 20 /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.03}, angular: {z: 0.0}}'
ros2 service call /nano_base/stop std_srvs/srv/Trigger '{}'
```

El mando solicita 3 cm/s; comprobar antes con las ruedas levantadas. Al dejar
de publicar, el puente detiene a los 300 ms y el Nano tiene además su watchdog
de 350 ms. Para otra prueba, habilitar de nuevo. No usar `--once` para mantener
movimiento: deliberadamente caducará.

Para una curva, elegir `v>0` y `w` que cumplan `v >= abs(w)*L/2` usando el `L`
medido. Ejemplo **solo para una separación de 0.30 m**: `v=0.03`, `w=0.10`
pide 0.015 m/s a la izquierda y 0.045 m/s a la derecha, por lo que gira a la
izquierda. Cambiar `w` a -0.10 produce una curva a la derecha. No copiar ese
ejemplo como medida del robot. `v=0, w!=0` se rechaza porque exige marcha atrás.

## 9. Comprobar odometría de verdad

```bash
ros2 topic echo /wheel_feedback
ros2 topic echo /odom
ros2 run tf2_ros tf2_echo odom base_link
```

Ejecutar cada observación en su terminal o detener la anterior con Ctrl+C.
Con el robot parado, los contadores deben permanecer constantes y la velocidad
medida ser cero. Al mover cada rueda se debe ver su FG; avanzando en recta,
`x` aumenta y el giro se mantiene próximo a cero. En una curva izquierda,
el yaw aumenta; hacia la derecha disminuye.

Para validar escala, hacer después una prueba supervisada sobre el suelo,
medir distancia real recorrida y compararla con odometría. Comprobar curvas
de ángulo conocido y ajustar separación efectiva si hay deslizamiento. La
primera validación sobre ruedas levantadas solo confirma señales, no distancia
real del chasis. Desconectar USB con ruedas elevadas debe detener motores;
`/odom` debe dejar de recibir muestras nuevas, y reconectar no debe reanudarlos.

No iniciar aún navegación autónoma: faltan validación bajo carga, integración
de IMU/LiDAR, localización y detección de obstáculos.
