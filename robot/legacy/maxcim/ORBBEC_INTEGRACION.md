# Integración Orbbec Gemini 2 — MAXCIM

Cámara: **Orbbec Gemini 2** (color RGB + depth sensor alineado)
Paquete nuevo: `orbbec_vision_pkg` (no se tocó `vision_pkg` estable — rollback disponible)

---

## Estado por fases

### ✅ Fase 1 — Nodos base + distancia real (COMPLETA)

**Problema resuelto (crítico):** `frame.get_data()` del SDK devuelve un numpy array con `stride=(0,)` — todos los bytes se ven iguales. Fix: `ctypes.string_at(data.ctypes.data, n_bytes)` para leer el buffer real de C++.

**Nodos implementados:**

#### `orbbec_camera_node`
```
ros2 run orbbec_vision_pkg orbbec_camera_node --ros-args -p width:=640 -p height:=480
```
- Usa `pyorbbecsdk` + `AlignFilter` (software D2C): profundidad proyectada al espacio del color
- Publica `camera/image_raw/compressed` (JPEG 640×480 @ 15 Hz)
- Publica `camera/depth/image_raw` (16UC1, mm alineado @ 15 Hz)
- Publica `camera/image_raw/camera_info` (intrínsecos, transient_local/latched)
- Color tomado del frameset **original** (antes del AlignFilter, que corrompe los datos de color)
- Sin cv_bridge (incompatible con NumPy 2.x en Jazzy)

#### `orbbec_face_recognition_node`
```
ros2 run orbbec_vision_pkg orbbec_face_recognition_node
```
- Copia de `vision_pkg/face_recognition_node` con cambios quirúrgicos
- Suscribe `camera/depth/image_raw` para obtener profundidad real
- `_depth_distance(face)`: mediana 5×5 px alrededor del centro del bbox → metros
- Fallback a `_estimate_distance` si el depth no está disponible

**Topics publicados (Fase 1):**
| Topic | Tipo | Hz | Descripción |
|-------|------|----|-------------|
| `camera/image_raw/compressed` | CompressedImage | 15 | Color JPEG |
| `camera/depth/image_raw` | Image (16UC1) | 15 | Depth mm |
| `camera/image_raw/camera_info` | CameraInfo | 15 | Intrínsecos (latched) |
| `vision/faces` | String (JSON) | 1 | Caras con distancia real |

---

### ✅ Fase 2 — Coordenadas 3D en `vision/faces` + tool para Gemini (COMPLETA)

**Actividad 1 — Coordenadas 3D en `vision/faces`**

Intrínsecas de la Orbbec Gemini 2 a 640×480: `fx=517.414  fy=517.423  cx=321.551  cy=238.426`

Campos añadidos al JSON de cada rostro:
```json
{
  "nombre": "Alexis",
  "distance": 1.29,
  "x_m": -0.104,
  "y_m":  0.106,
  "z_m":  1.29
}
```
- `z_m` = profundidad real del sensor (metros)
- `x_m` = posición lateral: positivo = derecha, negativo = izquierda
- `y_m` = posición vertical: positivo = abajo (coordenadas de cámara)
- `camera/image_raw/camera_info` publicado por `orbbec_camera_node`, recibido por `orbbec_face_recognition_node` vía suscripción transient_local

**Actividad 2 — Tool `obtener_posicion_espacial` para Gemini Live**

Archivo: `src/reasoning_pkg/reasoning_pkg/tools.py`

```python
@tool
def obtener_posicion_espacial() -> dict:
    ...
```

Lee `_latest_faces` (misma fuente que `identificar_personas`) y devuelve descripción textual:

> "Alexis está cerca (1.29 m), al frente y te mira directamente. desconocido está a distancia media (2.53 m), a tu izquierda."

Zonas de distancia: `muy cerca (<0.8m)` / `cerca (<1.5m)` / `a distancia media (<3m)` / `lejos (≥3m)`
Lateral: `a tu izquierda (x<-0.15m)` / `al frente` / `a tu derecha (x>0.15m)`

**Actividad 3 — Nodo de proximidad**

```
ros2 run orbbec_vision_pkg orbbec_proximity_node
```

| Topic | Tipo | Hz | Descripción |
|-------|------|----|-------------|
| `orbbec/proximity` | Float32 | 5 | Distancia mínima (m) en zona central 30% |
| `orbbec/presence` | Bool | 5 | True si alguien está dentro del umbral (2 m) |

Parámetros clave:
- `zone_fraction` (0.3): fracción del frame analizada
- `presence_threshold` (2.0): metros para considerar presencia
- `noise_percentile` (5.0): percentil para filtrar ruido de profundidad

---

### ✅ Fase 2 — Actividad 4 (COMPLETA)

**Depth overlay para Gemini Live**

`orbbec_camera_node` publica `camera/depth_overlay/compressed`: color + depth coloreado (COLORMAP_TURBO, alpha=0.3). Píxeles sin dato (0 mm) → negro. Parámetro `depth_overlay_alpha` ajustable.

`gemini_live_node` con `use_depth_overlay:=true` suscribe ese topic en lugar del color puro, e inyecta en el system instruction la explicación del colormap (rojo/amarillo=cerca, azul/violeta=lejos) para que Gemini lo interprete correctamente. En modo overlay, `obtener_posicion_espacial` sigue disponible para precisión exacta e identidad.

---

### 🔲 Fase 3 — Pendiente (no planificada en detalle)

Ideas para fases futuras:
- **Detección de presencia sin cara**: usar `orbbec/presence` como señal de "wakeup" en `gemini_live_node` para que MAXCIM inicie conversación al detectar a alguien acercándose
- **Segmentación de persona por depth**: aislar silueta de la persona más cercana para enfocar el análisis de Gemini
- **Tracking con depth**: re-identificar personas por su blob de profundidad cuando pierdan contacto visual con la cámara
- **Pose corporal con escala real**: estimación de posturas en metros, no solo en píxeles

---

## Orden de lanzamiento completo

```bash
set -a && source .env && set +a
source /opt/ros/jazzy/setup.bash && source install/setup.bash

# Terminal 1 — Cámara Orbbec
ros2 run orbbec_vision_pkg orbbec_camera_node --ros-args -p width:=640 -p height:=480

# Terminal 2 — Reconocimiento facial con depth
ros2 run orbbec_vision_pkg orbbec_face_recognition_node

# Terminal 3 — Proximidad / presencia
ros2 run orbbec_vision_pkg orbbec_proximity_node

# Terminal 4 — Micrófono
ros2 run audio_pkg mic_node

# Terminal 5 — Razonamiento MAXCIM
ros2 run reasoning_pkg gemini_live_node
```

> Rollback: sustituir terminales 1 y 2 por `vision_pkg camera_node` y `vision_pkg face_recognition_node`

---

## Notas técnicas críticas

| Problema | Causa | Solución |
|----------|-------|----------|
| Imagen gris uniforme | `frame.get_data()` retorna ndarray con `stride=(0,)` | `ctypes.string_at(data.ctypes.data, n_bytes)` |
| Color corrupto tras AlignFilter | AlignFilter modifica el buffer de color internamente | Tomar color del frameset **original**, depth del alineado |
| cv_bridge crash | Compilado contra NumPy 1.x, incompatible con 2.4.6 | Eliminado de ambos nodos; parseo manual de mensajes |
| Face detection falla a 1920×1080 | InsightFace det_size=320 reduce imagen a 1/6 | Fijar resolución a 640×480 al lanzar el camera_node |
| QoS mismatch | Camera usa BEST_EFFORT; suscriptores deben casar | `QoSReliabilityPolicy.BEST_EFFORT` en todos los subs |
