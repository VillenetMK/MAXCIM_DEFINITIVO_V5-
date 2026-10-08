# MAXCIM V5

Centro de operación y espacio de equipo para MAXCIM, **Proyecto de Innovación · SENATI · Dirección Zonal LAMBAYEQUE · ETIA**.

Consola web con acceso por rol, control exclusivo por operador/pestaña, estado por WebSocket, parada enclavada, tareas y bitácora persistentes. El robot utiliza un gateway ROS 2 que aplica límites y detiene órdenes vencidas aunque la API desaparezca. La cámara JPEG y el audio se declaran presentes solo al recibir datos recientes.

**Estado de esta entrega:** consola y simulación verificadas por 123 pruebas Python y dos bancos C++. Núcleo ROS 2 Jazzy compilado y ensayos DDS con actuadores/Nano emulados aprobados en GitHub Actions, junto con el recorrido real del panel en Chromium. Flasheo, servicios externos y pruebas físicas pendientes. Los motores y brazos requieren calibración explícita. Ningún sensor ni resultado experimental se presenta como real por estar simulado. Ver [validación](docs/VALIDATION.md).

## Arrancar la consola en simulación

Python 3.11+ y Node 22+ para las comprobaciones de JavaScript. Ejecutar desde la raíz:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m maxcim_api.admin gabriel --role admin
python -m maxcim_api.server
```

Abrir **http://127.0.0.1:8080**. La contraseña se solicita en terminal; no existen credenciales predeterminadas. El administrador crea las cuentas del equipo desde **Accesos**. Adquirir control, mantener pulsado **Avanzar** y soltar para frenar. **Detener** libera la concesión; **Parada de emergencia** exige rearme y nueva adquisición. Dos pestañas tienen identificadores distintos aunque compartan cuenta.

Las variables están en [.env.example](.env.example). La API usa `os.environ`: exportarlas en el entorno del proceso o usar el archivo de entorno del servicio. No carga `.env` automáticamente. La aplicación educativa sí utiliza su propio entorno.

## Estructura

| Ruta | Responsabilidad |
|---|---|
| `frontend/` | Panel adaptable, sin CDN ni compilador obligatorio |
| `backend/maxcim_api/` | HTTP, sesiones, roles, CSRF, auditoría, colaboración y puente ROS |
| `packages/maxcim_core/` | Reglas de control y resolución del catálogo; importaciones sin acceso a hardware |
| `robot/ros2/src/` | Interfaces, gateway, Nano, odometría FG, brazos, LiDAR, visión, audio, memoria y conversación |
| `robot/firmware/` | Firmware ESP32 V5: sin homing al arranque y con STOP/watchdog |
| `robot/base-reference/firmware/` | Firmware Nano y protocolo CRC de la versión más reciente recuperada |
| `apps/education/` | Plataforma docente original: contenidos, narración, institución y pruebas |
| `robot/legacy/` | Fuentes anteriores conservadas para consulta; excluidas de colcon y del arranque V5 |
| `docs/` | Arquitectura, conexiones originales, operación, procedencia y evidencia |
| `tests/`, `scripts/` | Pruebas, construcción por dispositivo y diagnóstico |

## Robot y equipo

La Raspberry ejecuta gateway, Nano/ESP32, LiDAR y odometría. La Jetson ejecuta cámara, visión, audio y conversación opcional. La consola puede ejecutarse en un equipo de la misma red con ROS 2. No iniciar simultáneamente los antiguos `SERV.py`, `control_web` o `studio`: tienen sus propias entradas a actuadores.

Consulta [arranque ROS 2 y red](docs/RUNBOOK.md), [arquitectura y contratos](docs/ARCHITECTURE.md), [hardware](docs/HARDWARE.md), [aplicación educativa](docs/EDUCATION.md) y [cómo contribuir](CONTRIBUTING.md).

```bash
bash scripts/check.sh
```

La comprobación local no necesita ROS ni el robot. La integración física requiere una distribución ROS compatible con el sistema real de ambas placas; esa versión aún debe confirmarse. Jazzy/Ubuntu 24.04 es el perfil de construcción propuesto, no un sistema operativo detectado en MAXCIM.

La base Nano recuperada **solo admite avance y curvas sin invertir ruedas**. No se han añadido órdenes de retroceso ni giro sobre el eje sin verificar el cableado y el sentido físico. Nav2 se conserva como referencia, con integración al gateway pendiente.

No se incluyen claves, bases de datos personales, modelos de reconocimiento ni grabaciones. Se mantienen las atribuciones originales; las licencias de módulos propios pendientes de aclaración se enumeran en [procedencia](docs/PROVENANCE.md).
