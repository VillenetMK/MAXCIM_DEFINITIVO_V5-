# Mapeo LiDAR sin IMU

Configuración: LiDAR 2D + odometría de encoders, ROS 2 Jazzy y SLAM Toolbox asíncrono. No se necesita IMU para este flujo. El código Arduino V3 enviado controla los motores y emite velocidades/contadores; no contiene una pausa de 16 segundos ni controla el LiDAR.

## Diagnóstico medido

El 24 de septiembre de 2026 se ejecutó únicamente el controlador del LiDAR durante 22 segundos, aislado del resto del robot. Se recibieron 159 escaneos, aproximadamente 7,75 Hz; el primer mensaje llegó a los 1,72 s y el intervalo máximo entre escaneos fue de 0,138 s. No se reprodujo el ciclo de 16 segundos en esa prueba.

Los registros anteriores mostraban una transformación `odom → laser` ausente. El lanzamiento tenía además una espera artificial inicial de 5 s, sondeo de puertos serie y un relay con el parámetro `steal_time`, que no forma parte de la interfaz documentada de `topic_tools relay`. Los parámetros `motor_pwm` y `use_system_time` tampoco son consumidos por el código del controlador RPLIDAR incluido. La configuración por defecto instalada de SLAM Toolbox publicaba el mapa cada 5 s; no se verificó si el usuario la utilizaba en la sesión con demora.

Estos hallazgos no demuestran por sí solos una causa única de los 16 s. La prueba del sensor distingue su frecuencia real del ritmo de construcción/visualización del mapa.

## Resultado de la prueba integrada

Con LiDAR real y odometría estacionaria simulada, se recibieron 138 escaneos, 816 mensajes de odometría y 33 mapas. El mapa se publicó cada **0,5001 s** en promedio, con intervalo máximo de **0,5027 s**. El primer mapa apareció a los **5,84 s** desde iniciar el lanzamiento completo: la actualización continua es rápida, pero el arranque no es instantáneo. Las transformaciones odom → laser y map → laser estuvieron disponibles.

Al iniciar se descartó un escaneo antiguo antes de que existiera odometría; no se observó acumulación sostenida de 16 s. El cierre de prueba por señal al grupo registró interrupción SIGINT del proceso de base. Los procesos de prueba se detuvieron al finalizar.

## Cambios

- `robotall.launch.py` inicia sus componentes sin `TimerAction` y publica el escaneo directamente en `/scan`, conservando la marca temporal de adquisición.
- `lidar.launch.py` selecciona el puerto sin abrir puertos de otros dispositivos para probarlos. Excluye los destinos de `/dev/arduino` y `/dev/esp32`; si hay ambigüedad exige `lidar_port`. En la sesión medida se usó `/dev/ttyUSB1`; esa numeración puede cambiar.
- `base_node.py` publica `odom → base_footprint`. El URDF publica `base_footprint → base_link → laser`. Antes, `base_link` recibía dos padres distintos.
- `telemetry.py` conserva fragmentos de línea entre lecturas serie. Acepta las velocidades `Kal_I`/`Kal_D` del firmware V3 solo cuando la línea está completa, y descarta datos inválidos.
- `mapping.launch.py` inicia SLAM Toolbox asíncrono sin IMU ni controladores Nav2. Puede utilizar `/scan` y odometría existentes, o arrancar también la base y el LiDAR con `bringup_robot:=true`.
- `slam_lidar.yaml`: objetivo de publicación del mapa cada 0,5 s, cola de un escaneo, mínimo temporal de 0,1 s y umbrales de desplazamiento/giro de 0,05 m y 0,05 rad. La cadencia real depende del procesamiento; configurar 0,5 s no garantiza latencia constante en todos los entornos.
- Vista RViz con marco fijo `map`, mapa y escaneo actual; no acumula puntos mediante un tiempo de persistencia largo.

La base sigue usando velocidades filtradas para odometría. No se sustituyeron los PID, no se añadió una IMU y no se grabó firmware en el Arduino.

## Iniciar en la Raspberry

La copia corregida y compilada está en `/home/maxcimrpi/MAX`. Los workspaces originales se conservaron.

Desde una terminal del escritorio de la Raspberry:

```bash
/home/maxcimrpi/MAX/start_mapping.sh
```

Este comando inicia el LiDAR, el nodo de base, SLAM Toolbox y RViz. El nodo de base abre el puerto del controlador; el lanzamiento no inicia navegación autónoma ni publica objetivos de movimiento. Cerrar con `Ctrl+C` en esa terminal.

Si los puertos necesitan indicarse explícitamente:

```bash
/home/maxcimrpi/MAX/start_mapping.sh lidar_port:=/dev/PUERTO_LIDAR arduino_port:=/dev/arduino
```

Para una terminal sin pantalla gráfica:

```bash
/home/maxcimrpi/MAX/start_mapping.sh rviz:=false
```

No ejecutar en paralelo con otro lanzamiento que ya abra los mismos puertos o publique la misma odometría. Para usar una base ya iniciada, cargar el nuevo workspace y ejecutar solo el mapeo:

```bash
source /opt/ros/jazzy/setup.bash
source /home/maxcimrpi/MAX/ros2_ws/install/setup.bash
ros2 launch robot_autonav mapping.launch.py rviz:=true
```

## Compilar una copia nueva

```bash
cd /home/maxcimrpi/MAX/ros2_ws
source /opt/ros/jazzy/setup.bash
colcon build --packages-select rplidar_ros robot_base robot_autonav --executor sequential
```

Se requieren los paquetes ROS y dependencias que ya están instalados en la Raspberry, incluido `slam_toolbox`. Un entorno vacío necesita instalarlos antes.

## Validación y límites

Se comprobó el ensamblado de tramas fragmentadas con cuatro pruebas automáticas sin hardware y la cadena TF sin padres duplicados. Los tres paquetes se compilaron en la Raspberry. El resultado de la prueba integrada figura en `validacion_lidar.json`: LiDAR real y odometría estacionaria simulada por un pseudopuerto serie, aislados en un dominio ROS de prueba. Los puertos reales de motores no se abrieron en esa prueba.

Esta validación mide publicación de escaneos/mapa y disponibilidad de TF; no valida precisión del mapa durante desplazamientos, calibración de ruedas, calidad de odometría física ni el renderizado de RViz en pantalla. La configuración nueva no se dejó ejecutándose al terminar.

## Referencias

- [SLAM Toolbox: parámetros y requerimientos TF](https://github.com/SteveMacenski/slam_toolbox).
- [topic_tools: interfaz de relay](https://github.com/ros-tooling/topic_tools).
- Código local de `ros2_ws/src/rplidar_ros/src/rplidar_node.cpp` y versión instalada de SLAM Toolbox 2.8.5.
