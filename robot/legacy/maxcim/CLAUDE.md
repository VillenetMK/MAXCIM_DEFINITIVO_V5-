# mciav2_ws — MAXCIM, robot con razonamiento multimodal en tiempo real

Workspace ROS 2 Jazzy (Ubuntu 24.04). Robot llamado **MAXCIM**, del Colegio CIMA, Chiclayo, Perú.
Visión + audio + razonamiento vía Gemini Live API + memoria persistente de usuarios y del propio robot.

## Arquitectura general

```
[mic_node] ──audio/raw──────────────────────────────────────────────────────┐
[orbbec_camera_node] ──camera/image_raw/compressed──┬───────────────────────┤
                    ──camera/depth/image_raw─────────┤                       │  [gemini_live_node]
                    ──camera/depth_overlay/compressed─┤  (use_depth_overlay)  │  ├─ Gemini Live WebSocket
                    ──camera/image_raw/camera_info───►│                       │  ├─ tools: identificar_personas
                                                      │                       │           obtener_posicion_espacial
[orbbec_face_recognition_node] ──vision/faces──[memory_node]                 │           buscar_memorias / sugerir_memoria
                                                      │  memory/user_context─►│           buscar_memoria_robot / guardar_memoria_robot
[orbbec_proximity_node] ──orbbec/proximity────────────┘                      │           registrar_persona / get_current_time
                        ──orbbec/presence                                     │
                          robot_general_memory (Qdrant) ────────────────────►│  (inyectado al arrancar sesión)
```

**Servicios externos (Docker):**
- Qdrant `localhost:6333` — memoria usuarios (`memorias_usuario`) + memoria robot (`robot_general_memory`)
- n8n `localhost:5678` — workflow embed+dedup+guardado para memorias de usuarios
- PostgreSQL `localhost:5432` (DB: `maxcim_general`) — embeddings faciales + tabla de usuarios

## Paquetes

| Paquete | Nodo(s) | Rol |
|---------|---------|-----|
| `audio_pkg` | `mic_node` | Captura PCM 16kHz via `arecord` → topic `audio/raw` (UInt8MultiArray) |
| `orbbec_vision_pkg` | `orbbec_camera_node` | Orbbec Gemini 2 (color+depth). JPEG 640×480 @ 15 Hz + depth 16UC1 mm alineado. Publica `camera_info` latched |
| `orbbec_vision_pkg` | `orbbec_face_recognition_node` | InsightFace buffalo_sc con depth real → `vision/faces` (JSON). Umbral adaptativo por distancia + liveness detection. Servicio `registrar_rostro` |
| `orbbec_vision_pkg` | `orbbec_proximity_node` | Distancia mínima zona central del depth → `orbbec/proximity` (Float32) y `orbbec/presence` (Bool) a 5 Hz |
| `memory_pkg` | `memory_node` | Suscribe `vision/faces`, carga memorias de usuario de Qdrant, publica `memory/user_context` |
| `reasoning_pkg` | `gemini_live_node` + `tools.py` | Gemini Live API. Audio+video streaming, tool calls, inyección de contexto de usuario y robot |
| `robot_interfaces` | — | Define `RegistrarRostro.srv` |
| `vision_pkg` | `camera_node`, `face_recognition_node` | Stack alternativo con webcam USB. Rollback disponible si no hay cámara Orbbec |

## Variables de entorno — `.env` (raíz del workspace)

```env
GOOGLE_API_KEY=...             # Gemini Live + embeddings (gemini-embedding-001)
QDRANT_URL=http://localhost:6333
N8N_URL=http://localhost:5678
N8N_API_KEY=
FACE_DB_HOST=localhost
FACE_DB_PORT=5432
FACE_DB_NAME=maxcim_general
FACE_DB_USER=maxcim_n8n
FACE_DB_PASSWORD=
```

> **Nunca subir `.env` a git.** Está en `.gitignore`.

## Build

```bash
# IMPORTANTE: el filesystem VBoxSF no soporta symlinks.
# NO usar --symlink-install en este equipo (ext4 sí lo soporta).
source /opt/ros/jazzy/setup.bash
colcon build --packages-select reasoning_pkg   # rebuild tras cambios en reasoning_pkg
# o para todos los paquetes:
colcon build
source install/setup.bash
```

## Launch (orden recomendado — stack Orbbec)

```bash
# Siempre cargar el entorno primero
set -a && source .env && set +a
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# Terminal 1 — Cámara Orbbec (color + depth)
ros2 run orbbec_vision_pkg orbbec_camera_node --ros-args -p width:=640 -p height:=480

# Terminal 2 — Reconocimiento facial con depth real
ros2 run orbbec_vision_pkg orbbec_face_recognition_node --ros-args \
  -p liveness_depth_std_min:=5.0 \
  -p similarity_threshold:=0.45 \
  -p track_confirm_threshold:=0.30 \
  -p track_match_radius:=0.6

# Terminal 3 — Proximidad / presencia
ros2 run orbbec_vision_pkg orbbec_proximity_node

# Terminal 4 — Memoria de usuarios
ros2 run memory_pkg memory_node

# Terminal 5 — Micrófono
ros2 run audio_pkg mic_node

# Terminal 6 — Razonamiento MAXCIM
ros2 run reasoning_pkg gemini_live_node

# Opcional: modo depth overlay (Gemini ve color + mapa de profundidad superpuesto)
ros2 run reasoning_pkg gemini_live_node --ros-args -p use_depth_overlay:=true
```

### Rollback a webcam USB (sin Orbbec)

Reemplazar terminales 1 y 2 por:
```bash
ros2 run vision_pkg camera_node --ros-args -p camera_index:=0 -p publish_rate:=10.0
ros2 run vision_pkg face_recognition_node
```
El terminal 3 (`orbbec_proximity_node`) no aplica. El resto igual.

## Servicios Docker

```bash
docker compose up -d        # levantar Qdrant + n8n
docker compose down
curl http://localhost:6333/collections   # verificar Qdrant
curl http://localhost:5678/healthz       # verificar n8n
```

## Qdrant — colecciones

### `memorias_usuario` — memoria personal de usuarios
Vectores **3072 dims**, modelo `gemini-embedding-001`, distancia Cosine.

```bash
curl -X PUT http://localhost:6333/collections/memorias_usuario \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":3072,"distance":"Cosine"}}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"user_id","field_schema":"keyword"}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"importance","field_schema":"float"}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"type","field_schema":"keyword"}'
```

Payload: `user_id` (str del BIGINT de PostgreSQL), `type`, `content`, `importance`, `created_at`, `last_accessed`, `access_count`, `source`, `tags`, `expires_at`

Tipos: `semantic | episodic | preference | emotional`

### `robot_general_memory` — memoria propia del robot
Vectores **3072 dims**, modelo `gemini-embedding-001`, distancia Cosine.

```bash
curl -X PUT http://localhost:6333/collections/robot_general_memory \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":3072,"distance":"Cosine"}}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"importance","field_schema":"float"}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"type","field_schema":"keyword"}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"robot_id","field_schema":"keyword"}'
```

Payload: `content`, `type`, `importance`, `tags`, `robot_id` (="mcia_v2"), `created_at`, `source`

Tipos: `learning | mission | experience | preference`

> **NOTA:** existe también `robot_particular_memory` (vacía, reservada para memorias contextualizadas por situación/lugar/horario — no implementada aún).

## PostgreSQL

```sql
-- DB: maxcim_general | user: maxcim_n8n | pass: [REDACTED]
CREATE TABLE registered_user (id SERIAL PRIMARY KEY, nombre TEXT);
CREATE TABLE user_embedding (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES registered_user(id),
    embedding REAL[]  -- 512-dim, modelo InsightFace buffalo_sc/w600k_mbf
);
```

Usuarios registrados (al 2026-06-22): ninguno — BD vaciada el 2026-06-22.

## Workflow n8n — memorias de usuarios

**Webhook POST `/webhook/memory-suggest`:**
1. Validar `auth_token contra N8N_API_KEY del entorno`
2. Embed `content` con `gemini-embedding-001` (3072 dims)
3. Dedup Qdrant: buscar por `user_id` + score > 0.92; si duplicado, descartar
4. Agente AI: clasificar importancia (0.0–1.0) y tags
5. Upsert en `memorias_usuario`

> Las memorias propias del robot NO pasan por n8n — se escriben directamente a Qdrant desde `gemini_live_node._save_robot_memory_proxy()`.

## Tools disponibles en MAXCIM

| Tool | Descripción |
|------|-------------|
| `get_current_time` | Fecha y hora actual del sistema |
| `identificar_personas` | Reconocimiento facial via InsightFace (lee `vision/faces`). Incluye nombre, similitud, look_at_me, distancia, liveness |
| `obtener_posicion_espacial` | Posición 3D (x_m, y_m, z_m) de cada persona usando depth real. Devuelve descripción textual de ubicación |
| `registrar_persona(nombre)` | Registra un rostro desconocido en PostgreSQL |
| `buscar_memorias(query, limit)` | Búsqueda semántica en memorias del usuario actual (Qdrant) |
| `sugerir_memoria(contenido, type_hint)` | Sugiere guardar dato del usuario → n8n → Qdrant |
| `buscar_memoria_robot(query, limit)` | Búsqueda semántica en memorias propias del robot (Qdrant) |
| `guardar_memoria_robot(contenido, tipo, importancia, tags)` | Guarda aprendizaje/misión del robot → Qdrant directo |

## Agregar una tool nueva

En `src/reasoning_pkg/reasoning_pkg/tools.py`:

```python
@tool
async def nombre_tool(param: str) -> dict:
    """Descripción que ve Gemini. Type hints → schema automático."""
    ...
    return {'resultado': '...'}
```

Después hacer rebuild: `colcon build --packages-select reasoning_pkg`
El dispatch en `gemini_live_node.py` es automático — no hay que tocarlo.

## Identidad de MAXCIM (system_instruction)

Definida en `gemini_live_node.py` como tres constantes:
- `MAXCIM_IDENTITY`: quién es (nombre, colegio, ciudad, actitud)
- `MAXCIM_INSTRUCTIONS`: instrucciones operativas (tools, comportamiento, interpretación de campos como `liveness`, `skip_reason`, `threshold_used`)
- `MAXCIM_DEPTH_OVERLAY_HINT`: explicación del colormap de profundidad superpuesto (se añade solo si `use_depth_overlay:=true`)
- `DEFAULT_SYSTEM_INSTRUCTION = MAXCIM_IDENTITY + '\n\n' + MAXCIM_INSTRUCTIONS`

Se fija al conectar la sesión Gemini Live y NO cambia durante la sesión.
Las memorias dinámicas del robot se inyectan via `send_client_content` al abrir sesión.

## Parámetros clave

### gemini_live_node
- `model`: `gemini-3.1-flash-live-preview`
- `voice_name`: `Puck` (opciones: Aoede, Charon, Fenrir, Kore, Puck)
- `video_fps`: 1.0 Hz enviados a Gemini
- `playback_device`: `pipewire`
- `use_depth_overlay`: `false` — si `true`, suscribe `camera/depth_overlay/compressed` en vez del color puro e inyecta `MAXCIM_DEPTH_OVERLAY_HINT` al system instruction
- `image_topic`: `camera/image_raw/compressed`
- `faces_topic`: `vision/faces`
- `memory_context_topic`: `memory/user_context`

### orbbec_face_recognition_node
- `similarity_threshold`: 0.45 (coseno; 0.5 era demasiado estricto para personas con 1 solo embedding — similitudes oscilan 0.37–0.50)
- `process_rate`: 1.0 Hz
- `det_size`: 320
- `use_gpu`: `false` (activar con `true` una vez compilado onnxruntime-gpu)
- **Umbral adaptativo por distancia (Actividad 1):**
  - `dist_min`: 0.4 m (caras más cerca → skip)
  - `dist_max`: 3.5 m (caras más lejos → skip)
  - `dist_far_threshold`: 2.5 m (a partir de aquí sube el umbral)
  - `threshold_far`: 0.58 (umbral para zona lejana)
- **Liveness detection anti-spoofing (Actividad 2):**
  - `liveness_check`: `true`
  - `liveness_depth_std_min`: 5.0 mm (recalibrado 2026-06-22; caras reales muestran 6–13 mm a 1–1.6 m; fotos ~2–4 mm; el umbral de 15 mm previo rechazaba caras reales)
  - `liveness_bbox_expand`: -0.2 (contrae el bbox para medir solo zona interior de la cara)
- **Tracking 3D entre frames (Actividad 3):**
  - `track_max_age`: 3.0 s (tiempo antes de eliminar un track sin actualización)
  - `track_match_radius`: 0.6 m (radio para asociar detección a track existente; 0.4 m previo era demasiado pequeño para el ruido de profundidad del sensor)
  - `track_confirm_threshold`: 0.30 (umbral contextual — cuando el track ya tiene identidad y el mejor match es esa misma persona, basta con 0.30 para confirmar; evita perder la identidad cuando la cara está girada)

### orbbec_camera_node
- `width`: 640, `height`: 480
- `depth_overlay_alpha`: 0.3 (transparencia del mapa de profundidad superpuesto)

### orbbec_proximity_node
- `presence_threshold`: 2.0 m
- `zone_fraction`: 0.3 (30% central del frame)
- `publish_rate`: 5.0 Hz

### memory_node
- `initial_load_types`: `semantic,preference`
- `initial_load_min_importance`: 0.7
- `user_change_debounce`: 2.0 s

## Topics relevantes

| Topic | Tipo | Publicado por | Consumido por |
|-------|------|--------------|---------------|
| `audio/raw` | UInt8MultiArray | `mic_node` | `gemini_live_node` |
| `camera/image_raw/compressed` | CompressedImage | `orbbec_camera_node` | `orbbec_face_recognition_node`, `gemini_live_node` |
| `camera/depth/image_raw` | Image (16UC1, mm) | `orbbec_camera_node` | `orbbec_face_recognition_node`, `orbbec_proximity_node` |
| `camera/depth_overlay/compressed` | CompressedImage | `orbbec_camera_node` | `gemini_live_node` (si `use_depth_overlay:=true`) |
| `camera/image_raw/camera_info` | CameraInfo | `orbbec_camera_node` | `orbbec_face_recognition_node` |
| `vision/faces` | String (JSON) | `orbbec_face_recognition_node` | `memory_node`, `gemini_live_node` (tool `identificar_personas`) |
| `orbbec/proximity` | Float32 | `orbbec_proximity_node` | (no conectado aún al pipeline principal) |
| `orbbec/presence` | Bool | `orbbec_proximity_node` | (no conectado aún — wakeup automático pendiente) |
| `memory/user_context` | String (JSON) | `memory_node` | `gemini_live_node` |

## JSON de `vision/faces` — campos por rostro

```json
{
  "nombre": "Alexis",
  "reconocido": true,
  "similitud": 0.812,
  "look_at_me": true,
  "distance": 1.29,
  "x_m": -0.104,
  "y_m": 0.106,
  "z_m": 1.29,
  "liveness": true,
  "liveness_std": 28.4,
  "threshold_used": 0.50,
  "skip_reason": null,
  "track_id": 3,
  "track_age": 0.0,
  "from_tracker": false
}
```

- `skip_reason`: `"distancia_fuera_de_rango"` | `"liveness_fail"` | `null`
- `threshold_used`: `0.58` cuando la persona estaba en zona lejana (> 2.5 m)
- `track_id`: entero único por persona desde que el nodo arranca
- `track_age`: segundos desde la última detección real de ese track (0 = detectado este frame)
- `from_tracker`: `true` cuando la identidad viene del track (no de reconocimiento en este frame); puede ser porque la detección falló o porque el tracker está extendiendo la identidad sin detección activa

## Dependencias pip

```bash
pip install insightface onnxruntime psycopg2-binary numpy opencv-python google-genai httpx
```

> `pyorbbecsdk` se instala aparte desde `/home/maxcim/pyorbbecsdk/` — no está en PyPI.
> Ver `SETUP_NUEVO_EQUIPO.md` para el proceso completo en un equipo nuevo.

## Decisiones de diseño

- **QoS sensor** (BEST_EFFORT, depth=1): cámara y rostros siempre frescos, nunca backlog
- **Hilo asyncio separado** en `gemini_live_node` y `memory_node`: ROS executor y asyncio no se bloquean
- **Pre-buffer de audio** 300ms antes de `aplay`: evita underruns en respuesta hablada
- **Catálogo de embeddings en RAM**: registro de rostro es inmediato, sin esperar refresh de BD
- **fire-and-forget a n8n**: `sugerir_memoria` no bloquea la conversación
- **`user_id` como str en Qdrant**: PostgreSQL BIGINT incompatible con keyword-match numérico
- **Memoria del robot directa a Qdrant** (sin n8n): generada por el modelo, no necesita validación externa
- **Sin cv_bridge**: incompatible con NumPy 2.x en Jazzy — parseo manual de mensajes en `orbbec_vision_pkg`
- **Color del frameset original**: `AlignFilter` de Orbbec corrompe el buffer de color — depth del alineado, color del original
- **Umbral adaptativo**: mismo umbral de coseno para cara a 0.8 m que a 3.5 m produce falsos positivos; se escala por zona de distancia
- **Liveness por variación de depth**: cara plana (foto/pantalla) tiene std < 15 mm en el bbox; cara real supera ese umbral
- **Tracking 3D**: `FaceTrack` asocia detecciones por distancia euclidiana en 3D (radio 0.4 m); extiende identidad conocida cuando InsightFace no reconoce en ese frame; publica entradas con `from_tracker: true` para personas cuyo track sigue activo pero no fueron detectadas

## Funcionalidades pendientes de implementar

Ver `FACE_RECOGNITION_MEJORAS.md` para diseño detallado:
- **Actividad 4 — Filtrado por depth antes de InsightFace**: enmascarar personas fuera del rango de interés
- **Actividad 5 — Registro guiado por ángulo**: capturar embeddings frontal + lateral para mejor cobertura

Ver `ORBBEC_INTEGRACION.md` Fase 3:
- **Wakeup por presencia**: conectar `orbbec/presence` a `gemini_live_node` para que MAXCIM inicie conversación al detectar a alguien acercándose
