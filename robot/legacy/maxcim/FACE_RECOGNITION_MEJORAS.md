# Mejoras de Reconocimiento Facial — MAXCIM Fase 3

Nodo objetivo: `orbbec_vision_pkg/orbbec_face_recognition_node`
Todas las mejoras explotan el depth real de la Orbbec Gemini 2 ya disponible en `camera/depth/image_raw`.

---

## Estado por actividades

### ✅ Base actual (referencia)

- InsightFace `buffalo_sc` (512-d), umbral coseno fijo `0.5`
- Distancia real via mediana 5×5 px del depth map
- Coordenadas 3D `(x_m, y_m, z_m)` publicadas en `vision/faces`
- Registro multi-frame con heurística de hablante
- Fallback a modelo estenopeico si depth no disponible

---

### ✅ Actividad 1 — Umbral adaptativo por distancia

**Problema:** se aplica el mismo umbral `0.5` a una cara a 0.8 m (nítida, embedding rico) y a una a 3.5 m (pequeña, ruidosa, embedding pobre). Resultado: falsos positivos a larga distancia y falsos negativos cuando la cara está muy encima.

**Solución:** escalar el umbral según `z_m` medido por el sensor.

| Rango | Umbral | Razón |
|-------|--------|-------|
| `< 0.4 m` | skip — no procesar | cara demasiado cerca, parcialmente fuera de frame |
| `0.4 – 2.5 m` | `0.50` | zona óptima — comportamiento actual |
| `2.5 – 3.5 m` | `0.58` | calidad de embedding cae — exigir más parecido |
| `> 3.5 m` | skip — publicar como desconocido | demasiado lejos para ser confiable |

**Implementación:** función `_adaptive_threshold(z_m) -> float | None` en `_process_once`, reemplaza el `self._threshold` fijo en la comparación con la matriz de embeddings.

**Parámetros nuevos:**
- `dist_min` (float, 0.4): distancia mínima de procesamiento
- `dist_max` (float, 3.5): distancia máxima de procesamiento
- `dist_far_threshold` (float, 2.5): punto donde empieza a subir el umbral
- `threshold_far` (float, 0.58): umbral aplicado entre `dist_far_threshold` y `dist_max`

**Esfuerzo:** muy bajo — ~15 líneas de código, sin dependencias nuevas.

---

### ✅ Actividad 2 — Liveness detection (anti-spoofing)

**Problema:** el sistema puede ser engañado con una foto o pantalla mostrando una cara. Una imagen plana tiene variación de profundidad casi cero en la región facial. Una cara real tiene la nariz sobresaliendo y mejillas/frente retrocediendo.

**Solución:** antes de comparar con el catálogo, verificar que el depth en el bbox de la cara tiene variación estadística mínima compatible con una cara 3D real.

```
std_dev(depth_valid en bbox) < liveness_depth_std_min → rechazar como "cara plana"
```

**Implementado:**
- Método `_liveness_check(face, depth)` → devuelve `(is_live: bool, std_mm: float)`
- Si `std < liveness_depth_std_min` → publica `liveness: false`, `skip_reason: liveness_fail`, sin intentar reconocer
- `self._liveness_enabled` (bool) guarda el parámetro — nombre separado del método para evitar shadowing

**Campo añadido al JSON de `vision/faces`:**
```json
{ "liveness": false, "liveness_std": 11.2, "skip_reason": "liveness_fail" }
```

**Parámetros:**
- `liveness_check` (bool, True): activa/desactiva el check
- `liveness_depth_std_min` (float, 15.0): std mínimo en mm para cara real
- `liveness_bbox_expand` (float, -0.2): factor de contracción del bbox — valor **negativo** para medir solo la zona interior de la cara, sin capturar manos ni fondo

**Resultado de calibración (2026-06-19):**

| Escenario | liveness_std | liveness |
|-----------|-------------|---------|
| Cara real a ~1.78 m | ~33 mm | true ✅ |
| Foto impresa a ~1.25 m | 10–12 mm | false ✅ |
| Cara real a ~0.92 m (muy cerca) | 3–5 mm | false ❌ (falso negativo) |

**Pendiente de calibración:** a distancias < 1 m el bbox contraído cae en zona de depth inconsistente del sensor. Opciones a explorar:
- Bajar `liveness_depth_std_min` a 8 mm
- Ajustar `liveness_bbox_expand` a -0.1 en vez de -0.2
- Combinar con `dist_min` para no aplicar liveness check si `z_m < 0.5 m` (ya se skipea por distancia)

---

### ✅ Actividad 3 — Tracking 3D entre frames

**Problema:** el reconocimiento corre a 1 Hz. Entre frames, si InsightFace no detecta la cara (oclusión parcial, movimiento brusco, giro), la identidad se pierde y el siguiente frame puede publicar "desconocido" por un instante. Esto genera parpadeo en `vision/faces` y puede provocar que Gemini salude de nuevo a alguien que ya conoce.

**Solución:** mantener un registro de tracks activos con su última posición 3D conocida. Si un rostro falla en un frame, buscar el track más cercano en 3D y extender su identidad por `track_max_age` segundos.

**Estructura del tracker:**
```python
@dataclass
class FaceTrack:
    track_id: int
    user_id: int | None
    name: str
    last_pos: tuple[float, float, float]  # x_m, y_m, z_m
    last_seen: float                       # time.monotonic()
    similarity: float
```

**Pipeline modificado en `_process_once`:**
1. InsightFace detecta caras → para cada cara, obtener embedding + posición 3D
2. Asociar cara detectada al track activo más cercano en 3D (distancia euclidiana < `track_match_radius`)
3. Si hay match de track: actualizar posición; si la similitud mejora, actualizar identidad
4. Si no hay match: crear track nuevo
5. Tracks sin actualización > `track_max_age` → eliminar
6. Publicar identidad del track (no solo del frame actual)

**Parámetros nuevos:**
- `track_max_age` (float, 3.0): segundos antes de eliminar un track sin actualización
- `track_match_radius` (float, 0.4): radio en metros para asociar cara a track existente

**Campo añadido al JSON:**
```json
{ "track_id": 7, "track_age": 0.8 }
```

**Esfuerzo:** medio — ~60 líneas. La lógica de asociación es la parte más delicada.

---

### 🔲 Actividad 4 — Filtrado por depth antes de InsightFace

**Problema:** en pasillos o aulas con movimiento, personas que pasan al fondo pueden interferir con la detección. También en escenas con reflejos o superficies con textura facial (pósters).

**Solución:** antes de pasar el frame a InsightFace, enmascarar los píxeles fuera del rango de profundidad de interés. Si ya se conoce la posición 3D del track (Actividad 3), filtrar solo alrededor de esa profundidad.

**Dos modos:**
- **Modo global** (sin tracks): solo procesar píxeles con `depth_min_mm < depth < depth_max_mm` (0.4–4.0 m)
- **Modo por track** (con Actividad 3): para cada track conocido, aplicar una máscara centrada en `z_track ± depth_band_mm`

**Implementación:** crear `_apply_depth_mask(frame_bgr, depth_mm, z_center=None) -> np.ndarray`. Píxeles fuera del rango → negro (InsightFace los ignora por contraste bajo).

**Parámetros nuevos:**
- `depth_filter` (bool, False): activa el filtro (False por defecto — es conservador)
- `depth_filter_min_m` (float, 0.4): mínimo
- `depth_filter_max_m` (float, 4.0): máximo
- `depth_band_m` (float, 0.5): banda alrededor de z_track en modo por-track

**Esfuerzo:** bajo-medio — ~30 líneas. Depende de Actividad 3 para el modo por-track.

---

### 🔲 Actividad 5 — Registro guiado por ángulo

**Problema:** los usuarios registrados solo tienen 1–6 embeddings tomados en un momento puntual. Si la persona vuelve de perfil o girada, la similitud cae. Un embedding frontal no cubre ángulos laterales.

**Solución:** durante el registro, guiar al usuario para capturar embeddings desde 3 ángulos: frontal, izquierda y derecha. InsightFace ya estima el `pose` (yaw, pitch, roll) en 2D — con depth podemos validarlo en 3D.

**Pipeline de registro modificado:**
```
Ángulos objetivo: frontal (yaw ≈ 0°), derecha (yaw ≈ +25°), izquierda (yaw ≈ -25°)
Para cada ángulo:
  1. MAXCIM dice: "ahora mira al frente / a tu derecha / a tu izquierda"
  2. Acumular frames con el ángulo correcto (±10° de tolerancia)
  3. Guardar embedding promedio de ese ángulo
```

**Cambios en el servicio `registrar_rostro`:**
- Nuevo campo en `RegistrarRostro.srv`: `guided_registration` (bool)
- Cuando `guided_registration=True`: el servicio devuelve mensajes intermedios de estado para que Gemini los vaya narrando al usuario

**Campo en `user_embedding` (PostgreSQL):**
```sql
ALTER TABLE user_embedding ADD COLUMN pose_yaw REAL DEFAULT NULL;
```
Permite saber qué ángulos ya tiene registrados cada usuario y cuáles faltan.

**Tool nueva `completar_registro(nombre)`:** verifica qué ángulos faltan para un usuario ya registrado y lanza el registro guiado solo para los que le faltan.

**Esfuerzo:** alto — requiere cambio en el srv, lógica de coordinación multi-paso y ajuste del sistema de registro existente. Implementar después de Actividades 1–3.

---

## Orden recomendado de implementación

| # | Actividad | Esfuerzo | Impacto | Dependencias |
|---|-----------|----------|---------|--------------|
| 1 | Umbral adaptativo por distancia | Muy bajo | Medio | — |
| 2 | Liveness detection | Bajo | Alto | — |
| 3 | Tracking 3D | Medio | Alto | — |
| 4 | Filtrado por depth | Bajo | Medio | Actividad 3 (modo por-track) |
| 5 | Registro guiado por ángulo | Alto | Alto | Actividades 1–3 estables |

Actividades 1 y 2 son independientes y pueden hacerse en paralelo. Actividad 3 desbloquea el modo avanzado de la 4.

---

## Parámetros nuevos — resumen

```bash
ros2 run orbbec_vision_pkg orbbec_face_recognition_node --ros-args \
  -p dist_min:=0.4 \
  -p dist_max:=3.5 \
  -p dist_far_threshold:=2.5 \
  -p threshold_far:=0.58 \
  -p liveness_check:=true \
  -p liveness_depth_std_min:=15.0 \
  -p track_max_age:=3.0 \
  -p track_match_radius:=0.4 \
  -p depth_filter:=false
```

---

## Campos nuevos en `vision/faces` (acumulado tras todas las actividades)

```json
{
  "nombre": "Alexis",
  "reconocido": true,
  "similitud": 0.812,
  "distance": 1.29,
  "x_m": -0.104,
  "y_m": 0.106,
  "z_m": 1.29,
  "look_at_me": true,
  "liveness": true,
  "liveness_std": 28.4,
  "track_id": 3,
  "track_age": 0.4,
  "threshold_used": 0.50
}
```
