# Control web de MAX

Interfaz estática en `control_web/static/` y puente WebSocket en `control_web/server.py`.
El puente publica `geometry_msgs/Twist` en `/cmd_vel`; el nodo de base existente conserva el acceso exclusivo al Arduino. No controla los brazos ni inicia navegación autónoma.

## Ejecutar en la Raspberry

Primero inicia el mapeo si no está activo:

```bash
bash /home/maxcimrpi/MAX/start_mapping.sh
```

En otra terminal, inicia el panel:

```bash
bash /home/maxcimrpi/MAX/start_control.sh --lan
```

Abre `http://127.0.0.1:8080` en la Raspberry. Desde un móvil u ordenador de la misma red, usa `http://IP_DE_LA_RASPBERRY:8080`. Consulta la IP con `hostname -I`.

El código de acceso se genera fuera del repositorio, con permisos 0600:

```bash
cat /home/maxcimrpi/.config/max-control/token
```

Es necesario introducirlo una vez por pestaña. Se conserva únicamente en sessionStorage de esa pestaña; al desconectarse no se reanuda el movimiento. El botón Conectar concede el control a una sola ventana. No hay confirmaciones para cada movimiento.

Mantén pulsado un botón o W/A/S/D o las flechas. Suelta para parar. Espacio o DETENER paran inmediatamente desde el navegador. Cambiar de ventana, cerrar la página, perder datos o desconectar también detienen las órdenes. Los deslizadores cambian la velocidad y detienen cualquier orden que estuviera activa.

## Instalación inicial

```bash
python3 -m venv --system-site-packages /home/maxcimrpi/.local/share/max-control/venv
/home/maxcimrpi/.local/share/max-control/venv/bin/pip install -r /home/maxcimrpi/MAX/control_web/requirements.txt
```

La opción `--system-site-packages` permite importar ROS Jazzy del equipo al cargar `/opt/ros/jazzy/setup.bash`.

La unidad `ops/systemd/max-control.service` permite arrancarlo sin depender de una terminal:

```bash
mkdir -p /home/maxcimrpi/.config/systemd/user
cp /home/maxcimrpi/MAX/ops/systemd/max-control.service /home/maxcimrpi/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user start max-control.service
```

No se habilita al encender el equipo. Para detenerlo: `systemctl --user stop max-control.service`.

## Publicación estática en GitHub

El workflow manual `control-pages.yml` publica únicamente `control_web/static`, sin otros archivos del robot. GitHub Pages debe estar habilitado con GitHub Actions; en repositorios privados requiere un plan compatible. No cambia la visibilidad del repositorio.

Una página HTTPS no se conecta directamente a un WebSocket HTTP de la LAN. El panel publicado permite introducir la dirección local y abrir el mismo panel alojado en la Raspberry. Para control remoto desde Internet haría falta una red privada o un proxy HTTPS autenticado; no se abre ni publica el puente del robot en Internet.

## Límites y pruebas

- Velocidad inicial: 0,05 m/s y 0,25 rad/s. Límites del servidor: 0,15 m/s y 0,40 rad/s.
- Órdenes a 10 Hz; temporizador ROS a 20 Hz; parada tras 0,30 s sin orden válida. Los tiempos dependen de la planificación del sistema, no son una garantía de tiempo real.
- Cada orden necesita un desafío del servidor con vigencia de 0,35 s, de un solo uso, y una secuencia creciente. Se rechazan órdenes demoradas, repetidas, no finitas o fuera de rango.
- Se exige odometría reciente (0,5 s), un escaneo reciente (1 s) y ausencia de otros publicadores de `/cmd_vel`. Los escaneos habilitan el control, pero NO implementan evitación de obstáculos.
- Cierre de conexión y parada invalidan los desafíos pendientes. Las pruebas usan un controlador simulado, sin publicar movimiento en `/cmd_vel`.
- El Arduino V3/V4 conservado no implementa un watchdog propio. Si falla la comunicación serie o el nodo de base, la última orden podría mantenerse. El botón web no sustituye un corte físico. Añadir y validar ese watchdog requiere una intervención separada en el firmware.
- HTTP local lleva el código por la red sin TLS: usar una red de confianza. Se validan Host, Origin y autenticación; solo se enlazan interfaces IPv4 privadas explícitas y loopback. No se abren puertos del router.

Pruebas del temporizador, sesiones, datos y límites:

```bash
python3 -m unittest discover -s /home/maxcimrpi/MAX/control_web/tests -v
```

Modo de interfaz sin ROS ni hardware:

```bash
/home/maxcimrpi/.local/share/max-control/venv/bin/python /home/maxcimrpi/MAX/control_web/server.py --demo --port 8081
```

## Nueva posición del LiDAR

La foto del 25/09/2026 indica un cambio físico. No se han inferido medidas de la imagen. El URDF aún tiene `base_footprint → base_link = (0,0,0.10)` y `base_link → laser = (0.10,0,0.20)`, sin rotación: altura total de 0,30 m. Medir la posición del centro del sensor respecto al centro del eje de ruedas y su orientación antes de actualizar esos valores. La calidad del mapa en movimiento depende de esa calibración y aún requiere una prueba física.

## Verificación del 25/09/2026

Ocho pruebas del controlador pasaron. Se probó HTTP/WebSocket con un controlador simulado: autenticación, origen, Host, exclusión de otra sesión, movimiento, parada y desconexión. Firefox en modo de demostración verificó W y su liberación, liberación del puntero fuera del botón, pérdida de foco, desconexión y ausencia de desbordamiento horizontal con ventana de 390 px. No se enviaron órdenes de velocidad distintas de cero a ROS. En el servicio real se verificaron autenticación, lectura de odometría/LiDAR y parada.

GitHub Pages no se activó: la revisión automática exigió autorización explícita para publicar una interfaz desde este repositorio privado. El workflow solo tiene ejecución manual y la publicación queda pendiente.

Se verificó además la confirmación de parada entre pulsaciones consecutivas: avanzar, girar a la izquierda y retroceder alcanzaron el controlador simulado con los signos correctos, y cada liberación volvió a cero.
