# Evidencia de validación

Entrega preparada el 8 de octubre de 2026. Las fechas del código importado no prueban que un ensayo se haya realizado en esas fechas. Esta entrega tampoco demuestra resultados obtenidos entre enero y julio para la tesis.

| Comprobación | Resultado |
|---|---|
| API/control/catálogo/callbacks ROS con dobles | 39 pruebas Python aprobadas |
| Protocolo, cinemática, seguridad y odometría de la base recuperada | 85 pruebas Python aprobadas |
| Firmware Nano, núcleo nativo C++ | Aprobado con `-Wall -Wextra -Werror -pedantic` |
| Sketch ESP32 V5 con dobles Serial/PCA9685 | Aprobado: parser, inicio deshabilitado, INIT, STOP, ID de grupo, rangos y watchdog |
| Sintaxis de API, paquetes y educación | compileall aprobado |
| JavaScript del panel | node --check aprobado |
| Instalación editable del paquete Python | Aprobada sin dependencias adicionales |
| Recorrido Chromium | Aprobado en GitHub Actions: control, dos pestañas, tareas/notas, móvil y logout |
| Núcleo ROS Jazzy y mensajes generados | Aprobado en GitHub Actions |
| DDS: puente Nano/odometría con PTY y gateway V5 con actuadores simulados | Aprobado en GitHub Actions; no prueba motores reales |
| Aplicación educativa Flask/SQLite aislada | Aprobada en GitHub Actions, ejecución 37819342478 |
| APIs institucionales/Gemini/Fish Audio | Pendiente: servicios reales no conectados |
| Compilación Arduino/ESP32 y flasheo | Pendiente; el banco nativo no utiliza el SDK real |
| Robot, LiDAR, cámara, audio y parada eléctrica | Pendiente: sin conexión física al robot |

Las 124 pruebas Python comprueban exclusión de operadores/pestañas, expiración de mando, orden atrasada después de frenar, enclavamiento, roles, CSRF, logout sin gateway, revocación de WebSocket, conflicto de tareas y catálogo real. También prueban que una respuesta vieja de ESP32 no confirme un grupo nuevo y que la voz no adquiera ni eluda el control del operador.

La ejecución [37817829022](https://github.com/VillenetMK/MAXCIM_DEFINITIVO_V5-/actions/runs/37817829022), revisión `1e6d7a400d38bd620c038f6b821908ac24ddd549`, aprobó los trabajos `core-and-browser` y `ros-build`. La compilación inicial cubrió interfaces, gateway, base y odometría. La ejecución [37818315019](https://github.com/VillenetMK/MAXCIM_DEFINITIVO_V5-/actions/runs/37818315019), revisión `b4b6db452f9d6a911f9478e2ef980aca1cd47666`, también aprobó la compilación de todo el workspace, incluido SDK LiDAR y paquetes Jetson. Compilar Python no equivale a cargar modelos, SDK de cámara o servicios reales.

La ejecución [37819342478](https://github.com/VillenetMK/MAXCIM_DEFINITIVO_V5-/actions/runs/37819342478), revisión `ff25672dafa3878b11d613b197608999c752143d`, aprobó los tres trabajos: núcleo/navegador, workspace ROS y aplicación educativa.

## Continuación: conexiones de estado lentas

Se reprodujo mediante un doble de WebSocket un cierre bloqueado que impedía al simulador vencer la orden de avance: tras 450 ms la velocidad seguía en 0,1 m/s. La regresión ahora comprueba la parada y la recepción de estado en otro cliente. El watchdog se ejecuta en una tarea separada; el envío a los clientes es concurrente y cada envío/cierre tiene un límite de 100 ms. Al apagar la API se solicita la parada antes de cerrar los WebSockets.

Esta separación evita la espera de red observada; no convierte el event loop de Python en un sistema de tiempo real. El gateway ROS y los watchdogs de firmware conservan su responsabilidad sobre el hardware. La prueba no mueve el robot físico.

## Repetir

```bash
bash scripts/check.sh
```

El recorrido del navegador usa `scripts/browser-check.cjs` y una cuenta temporal externa; no incluye claves en el repositorio. La CI preparada instala el navegador y ensaya login, avance mantenido, frenado, emergencia/rearme, dos pestañas, tareas/notas, vista móvil y logout. Consultar el resultado de Actions; la existencia del workflow no significa que haya pasado.

## Bloqueos para una entrega física

1. Confirmar SO/ROS/JetPack, identidad de dispositivos y puertos.
2. Calibrar ruedas/FG/PI y servos; confirmar el cableado y sentido del Nano nuevo.
3. Compilar y probar interfaces ROS reales, ACK serie y watchdogs con alimentación controlada.
4. Medir geometría LiDAR/cámara, envolvente de brazos y distancia de frenado; verificar parada eléctrica.
5. Configurar y probar servicios educativos/conversación sin usar resultados de demo como resultados reales.
6. Integrar Nav2 únicamente a través de una autorización del gateway; permanece como referencia heredada.

Estas tareas están pendientes, no escondidas detrás de indicadores verdes. Registrar sus resultados en la bitácora junto con la revisión git utilizada.
