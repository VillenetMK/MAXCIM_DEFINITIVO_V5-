# Setup en nuevo equipo — MAXCIM mciav2_ws

Guía para replicar el sistema completo desde cero. Leer también `CLAUDE.md` para arquitectura completa.

---

## 1. Prerequisitos del sistema

```bash
# ROS 2 Jazzy (Ubuntu 24.04)
# Python 3.12+
# Docker + Docker Compose

pip install insightface onnxruntime psycopg2-binary numpy opencv-python google-genai httpx

# pyorbbecsdk (no está en PyPI — clonar e instalar manualmente):
# git clone https://github.com/orbbec/pyorbbecsdk
# cd pyorbbecsdk && pip install -e .
# En Jetson Orin ya está en /home/maxcim/pyorbbecsdk/
```

---

## 2. Clonar/copiar el workspace

```bash
# Copiar todo el directorio mciav2_ws al nuevo equipo
# Asegurarse de incluir: src/, docker-compose.yml, .env (NO está en git)
```

---

## 3. Crear el `.env`

```bash
# En la raíz del workspace (mciav2_ws/.env):
GOOGLE_API_KEY=<tu_api_key_de_google_ai_studio>
QDRANT_URL=http://localhost:6333
N8N_URL=http://localhost:5678
N8N_API_KEY=
FACE_DB_HOST=localhost
FACE_DB_PORT=5432
FACE_DB_NAME=maxcim_general
FACE_DB_USER=maxcim_n8n
FACE_DB_PASSWORD=
```

---

## 4. Levantar servicios Docker

```bash
docker compose up -d

# Verificar:
curl http://localhost:6333/collections
curl http://localhost:5678/healthz
```

---

## 5. Crear base de datos PostgreSQL

```sql
-- Conectar como superuser y crear:
CREATE DATABASE maxcim_general;
CREATE USER maxcim_n8n WITH PASSWORD 'REPLACE_WITH_LOCAL_SECRET';
GRANT ALL PRIVILEGES ON DATABASE maxcim_general TO maxcim_n8n;

-- Dentro de maxcim_general:
\c maxcim_general
CREATE TABLE registered_user (id SERIAL PRIMARY KEY, nombre TEXT);
CREATE TABLE user_embedding (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES registered_user(id),
    embedding REAL[]
);
GRANT ALL ON ALL TABLES IN SCHEMA public TO maxcim_n8n;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO maxcim_n8n;
```

---

## 6. Inicializar colecciones Qdrant

```bash
# Memoria de usuarios
curl -X PUT http://localhost:6333/collections/memorias_usuario \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":3072,"distance":"Cosine"}}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"user_id","field_schema":"keyword"}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"importance","field_schema":"float"}'
curl -X PUT http://localhost:6333/collections/memorias_usuario/index \
  -H 'Content-Type: application/json' -d '{"field_name":"type","field_schema":"keyword"}'

# Memoria propia del robot
curl -X PUT http://localhost:6333/collections/robot_general_memory \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":3072,"distance":"Cosine"}}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"importance","field_schema":"float"}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"type","field_schema":"keyword"}'
curl -X PUT http://localhost:6333/collections/robot_general_memory/index \
  -H 'Content-Type: application/json' -d '{"field_name":"robot_id","field_schema":"keyword"}'

# (Opcional) Colección reservada para memorias contextuales del robot
curl -X PUT http://localhost:6333/collections/robot_particular_memory \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":3072,"distance":"Cosine"}}'
```

---

## 7. Configurar workflow n8n

Abrir http://localhost:5678 y crear el workflow manualmente:

**Trigger:** Webhook POST en `/webhook/memory-suggest`

**Pasos:**
1. **Validar auth**: `{{ $json.auth_token }} == ` → continuar o rechazar
2. **Embed**: llamar a Google AI API con `gemini-embedding-001`, input = `{{ $json.content }}`
3. **Dedup**: buscar en Qdrant `memorias_usuario` filtrando por `user_id` + `score_threshold: 0.92`; si hay resultado, terminar
4. **Clasificar**: AI agent que asigna `importance` (0.0-1.0) y `tags[]` según el contenido
5. **Upsert Qdrant**: PUT en `/collections/memorias_usuario/points` con payload completo

Payload que recibe el webhook:
```json
{
  "user_id": "2",
  "content": "texto a recordar",
  "type_hint": "semantic|episodic|preference|emotional",
  "timestamp": "2026-06-16T...",
  "auth_token": "REPLACE_WITH_LOCAL_SECRET"
}
```

---

## 8. Build ROS 2

```bash
cd mciav2_ws
source /opt/ros/jazzy/setup.bash

# IMPORTANTE: en filesystems VBoxSF (VirtualBox shared folder) NO usar --symlink-install
# En un equipo normal con ext4/etc. sí se puede usar --symlink-install
colcon build
# o si el filesystem lo permite:
# colcon build --symlink-install

source install/setup.bash
```

---

## 9. Lanzar el sistema

```bash
set -a && source .env && set +a
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# Stack Orbbec (principal) — en terminales separadas:
ros2 run orbbec_vision_pkg orbbec_camera_node --ros-args -p width:=640 -p height:=480
ros2 run orbbec_vision_pkg orbbec_face_recognition_node
ros2 run orbbec_vision_pkg orbbec_proximity_node
ros2 run memory_pkg memory_node
ros2 run audio_pkg mic_node
ros2 run reasoning_pkg gemini_live_node

# Rollback a webcam USB (sin cámara Orbbec):
# ros2 run vision_pkg camera_node --ros-args -p camera_index:=0 -p publish_rate:=10.0
# ros2 run vision_pkg face_recognition_node
```

---

## 10. Registrar personas

Al lanzar el sistema, hablar con MAXCIM y decirle tu nombre. El robot preguntará si quieres registrarte y lo hará automáticamente via la tool `registrar_persona`.

---

## Resumen de cambios implementados (sesión 2026-06-16)

Esta sección documenta qué se cambió respecto al código original para que otro Claude entienda el estado actual.

### Memoria propia del robot — Opción D híbrida

**Problema resuelto:** el robot no tenía memoria sobre sí mismo entre sesiones.

**Solución implementada:**

#### `src/reasoning_pkg/reasoning_pkg/tools.py`

Se añadieron 3 elementos:

```python
# 1. Global + setter para búsqueda
_search_robot_memory_fn = None
def set_search_robot_memory_fn(fn): ...

# 2. Global + setter para escritura
_save_robot_memory_fn = None
def set_robot_memory_fn(fn): ...

# 3. Dos tools nuevas decoradas con @tool:
async def buscar_memoria_robot(query: str, limit: int = 5) -> dict:
    # Genera embedding de query → busca en robot_general_memory en Qdrant
    # Usa _genai_client (ya existente) y _search_robot_memory_fn

async def guardar_memoria_robot(contenido, tipo, importancia, tags) -> dict:
    # Valida tipo en {learning, mission, experience, preference}
    # Delega en _save_robot_memory_fn
```

#### `src/reasoning_pkg/reasoning_pkg/gemini_live_node.py`

Se reemplazó el `DEFAULT_SYSTEM_INSTRUCTION` monolítico por:

```python
MAXCIM_IDENTITY = "Soy MAXCIM, robot del Colegio CIMA en Chiclayo, Perú..."
MAXCIM_INSTRUCTIONS = "Instrucciones operativas: tools, comportamiento..."
DEFAULT_SYSTEM_INSTRUCTION = MAXCIM_IDENTITY + '\n\n' + MAXCIM_INSTRUCTIONS
```

Se añadieron en `__init__` (tras `set_memory_fns`):
```python
tools_mod.set_robot_memory_fn(self._save_robot_memory_proxy)
tools_mod.set_search_robot_memory_fn(self._search_robot_memory_proxy)
```

Se añadieron 3 métodos async nuevos:
- `_search_robot_memory_proxy(vector, limit)` → POST a Qdrant `robot_general_memory/points/search`
- `_load_robot_memories()` → POST a Qdrant `robot_general_memory/points/scroll` (importance≥0.6, limit=15)
- `_save_robot_memory_proxy(content, tipo, importancia, tags)` → embed con `gemini-embedding-001` + PUT a Qdrant

En `_run_session()`, justo después de `self._session = session`:
```python
robot_memories = await self._load_robot_memories()
if robot_memories:
    # construir texto y send_client_content(...)
```

**Patrón de la memoria del robot:**
- Lectura al arrancar: scroll Qdrant por importancia (sin embed), inyectada via `send_client_content`
- Lectura en sesión: `buscar_memoria_robot` → embed → Qdrant search semántica
- Escritura: `guardar_memoria_robot` → embed en nodo → PUT directo a Qdrant (sin n8n)

**Por qué sin n8n para el robot:** las memorias del robot las genera el propio modelo, no necesitan validación ni dedup agresivo. Escritura directa es más simple y rápida.

### Tools completas al 2026-06-16

```
get_current_time
identificar_personas
registrar_persona
buscar_memorias          ← memoria de usuarios (via _search_memories_fn)
sugerir_memoria          ← sugiere guardar memoria de usuario → n8n
buscar_memoria_robot     ← NUEVO: búsqueda semántica en robot_general_memory
guardar_memoria_robot    ← NUEVO: guarda aprendizaje/misión del robot → Qdrant directo
```
