# Puesta en marcha

## 1. Simulación y cuentas

Seguir el README. El servicio guarda equipo/sesiones en `var/team.db`. Respaldar SQLite mediante su API de backup, no copiar únicamente el archivo principal mientras WAL está activo. Ejecutar una sola instancia de API por base de datos y robot. Antes de compartir en red: crear cuentas individuales y servir el panel por HTTPS; configurar el origen permitido exacto y `MAXCIM_COOKIE_SECURE=true`.

## 2. Preparar ROS 2

Confirmar primero el sistema real. El perfil propuesto es Ubuntu 24.04/ROS 2 Jazzy. [Instalación oficial](https://docs.ros.org/en/jazzy/Installation.html). No reemplazar el SO de una placa automáticamente. Ambas placas deben usar una distribución y middleware compatibles, la misma `ROS_DOMAIN_ID` y reloj sincronizado.

```bash
source /opt/ros/jazzy/setup.bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e '.[hardware]'
export ROS_DOMAIN_ID=42
bash scripts/build-ros.sh simulation
source robot/ros2/install/setup.bash
ros2 launch maxcim_control control.launch.py
```

En otro terminal con ROS, venv y overlay cargados:

```bash
export ROS_DOMAIN_ID=42
MAXCIM_MODE=ros2 python -m maxcim_api.server
```

El panel debe decir **SIMULACIÓN**, porque el gateway anterior usa actuadores simulados. Comprobar `ros2 topic echo /maxcim/state --once` y adquisición/parada antes del perfil físico.

## 3. Raspberry y base física

Construir con `bash scripts/build-ros.sh raspberry`. Copiar los perfiles de `maxcim_base/config/hardware.yaml` y `maxcim_odometry/config/hardware.yaml` a archivos locales ignorados. Completar puertos y mediciones. Revisar [protocolo Nano](../robot/base-reference/docs/PROTOCOL.md) y [puesta en marcha del banco](../robot/base-reference/docs/PUESTA_EN_MARCHA.md) antes de flashear. Los flags de cableado/PI/calibración se mantienen en falso de fábrica.

Ejemplo de arranque, sustituyendo las rutas por las reales:

```bash
ros2 launch maxcim_control raspberry.launch.py \
  base:=true base_config:="$(pwd)/config/base.local.yaml" \
  odometry_config:="$(pwd)/config/odometry.local.yaml" \
  lidar:=true lidar_port:=/dev/serial/by-id/PUERTO_LIDAR
```

El gateway arranca enclavado. Tras comprobar telemetría y entorno, habilitar el puente:

```bash
ros2 service call /nano_base/enable std_srvs/srv/SetBool '{data: true}'
```

La habilitación de periféricos es una operación del técnico desde ROS, separada de la concesión del panel. Rearmar y adquirir desde la consola. No ejecutar el puente CSV antiguo ni conectar Nav2 al topic privado `/maxcim/base/cmd_vel`.

La pérdida de feedback deshabilita el Nano y exige habilitar otra vez después de revisar el motivo. El panel no rearma por reconexión automática. Retroceso y giro sobre el eje no están disponibles en este protocolo.

## 4. Brazos

El nodo V5 solo reconoce **MAXCIM_ARMS 5**; no usarlo con el firmware antiguo. El sketch está en `robot/firmware/maxcim_arms_v5/`, requiere ESP32, Wire y Adafruit PWM Servo Driver. El banco C++ comprueba lógica con dobles; compilar con Arduino/ESP32 y probar con alimentación controlada sigue pendiente.

Completar una copia local de `arms.calibration.json`; medir pulsos por servo y home, probar todos los gestos y marcar `verified` solamente tras esa comprobación. Mantener los brazos sostenidos y en su home medido antes de habilitar. No deducir posiciones mecánicas por el valor interno de PWM.

```bash
ros2 run maxcim_control arms --ros-args \
  -p serial_port:=/dev/serial/by-id/PUERTO_ESP32 \
  -p calibration_file:="$(pwd)/config/arms.local.json" \
  -p catalog_dir:="$(pwd)/robot/legacy/BRAZOS_MAXICM_v8/data"
ros2 service call /maxcim_arms/enable std_srvs/srv/SetBool '{data: true}'
```

La solicitud de habilitación no confirma por sí sola que ENABLE se ejecutó: consultar `/maxcim/arms/state` hasta `enabled:true`. STOP cancela la cola y mantiene último PWM; `enable {data:false}` quita PWM y requiere sostener el peso de los brazos. La parada física del robot se comprueba por separado.

## 5. Jetson

Construir con `bash scripts/build-ros.sh jetson`. El SDK Orbbec, modelos de visión, OpenCV/InsightFace/ONNX y dependencias de Gemini deben instalarse para el hardware/JetPack real; no se incluyen modelos ni una selección CUDA inventada. Instalar los requisitos de conversación/memoria opcionales con `python -m pip install -r robot/requirements-jetson.txt` y configurar claves exclusivamente en el entorno.

```bash
ros2 launch maxcim_control jetson.launch.py camera:=true audio:=true
```

`faces`, `memory` y `conversation` son false por defecto. `faces:=true` requiere PostgreSQL/modelos de embeddings configurados; `memory:=true` requiere Qdrant/n8n; `conversation:=true` requiere Google API, modelo habilitado y ALSA. No se garantiza que el nombre de modelo recuperado siga disponible: confirmar su acceso en la cuenta antes de arrancar.

El panel debe recibir JPEG y audio recientes. Por defecto el audio usa `pipewire`; si el sistema es headless, iniciar `ros2 run audio_pkg mic_node --ros-args -p device:=DISPOSITIVO_ALSA` y desactivar `audio` en el launch para evitar abrir dos micrófonos. La pantalla HDMI muestra el panel en un navegador de la Jetson.

## 6. Compartir el panel

La API puede escucharse en `0.0.0.0` dentro de la red autorizada, detrás de un proxy TLS con WebSocket. Ejemplo de entorno: `MAXCIM_ALLOWED_ORIGINS=https://maxcim.ejemplo.local`, `MAXCIM_COOKIE_SECURE=true`. Registrar el nombre real; el dominio anterior es un marcador, no una URL desplegada. Los IDs de cuenta del panel no sustituyen las credenciales docentes institucionales.

`compose.yaml` ofrece una consola **en simulación** persistente, publicada en localhost. El modo físico se ejecuta en el host con ROS/overlay, no dentro de esa imagen Python sin ROS.

## Diagnóstico y ensayo de aceptación

```bash
python scripts/doctor.py
ros2 topic list
ros2 topic hz /scan
ros2 topic echo /maxcim/arms/state --once
ros2 topic echo /nano_base/status --once
```

Registrar en la bitácora: versión desplegada, calibraciones, operador, prueba de soltar el mando, pérdida de red, pérdida de serie, parada física, LiDAR inválido/obstáculo y recuperación sin arranque espontáneo. Después: odometría contra distancia medida y pruebas educativas. No mover en presencia de niños durante calibración. Nav2, envolvente de brazos y recorrido mecánico requieren ensayos adicionales antes de conducción autónoma.
