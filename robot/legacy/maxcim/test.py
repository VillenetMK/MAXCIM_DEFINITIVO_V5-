import os

_CUDA_DLL_DIRS = [
    r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.3\bin\x64",
    r"C:\Users\CIMA\AppData\Local\Programs\Python\Python312\Lib\site-packages\nvidia\cu13\bin\x86_64",
    r"C:\Users\CIMA\AppData\Local\Programs\Python\Python312\Lib\site-packages\nvidia\cudnn\bin",
]
_extra = ";".join(d for d in _CUDA_DLL_DIRS if os.path.isdir(d))
os.environ["PATH"] = _extra + ";" + os.environ.get("PATH", "")
for _d in _CUDA_DLL_DIRS:
    if os.path.isdir(_d):
        os.add_dll_directory(_d)

import threading
import queue
import time
from dataclasses import dataclass
import cv2
import numpy as np
from pyorbbecsdk import Pipeline, Config, OBSensorType, OBFormat, OBAlignMode
from insightface.app import FaceAnalysis

# ── Rangos de profundidad ──────────────────────────────────────────────────────
DEPTH_MIN_MM  = 400
DEPTH_MAX_MM  = 3500
DIST_FAR_M    = 2.5    # a partir de aquí sube el umbral de reconocimiento
THRESH_NEAR   = 0.50   # zona óptima  (0.4 – 2.5 m)
THRESH_FAR    = 0.58   # zona lejana  (2.5 – 3.5 m)
LIVENESS_STD  = 15.0   # std mínimo en mm para cara real vs foto

DB_PATH = os.path.join(os.path.dirname(__file__), "faces_db.npz")


# ── Base de datos de rostros ───────────────────────────────────────────────────

class FaceDB:
    def __init__(self, path: str):
        self.path = path
        # { nombre: np.array shape (N, 512) }
        self._db: dict[str, np.ndarray] = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            data = np.load(self.path, allow_pickle=False)
            for key in data.files:
                self._db[key] = data[key]
            print(f"DB cargada: {list(self._db.keys())}")
        else:
            print("DB vacía — registra personas con la tecla 'r'.")

    def save(self):
        np.savez(self.path, **self._db)

    def register(self, name: str, embedding: np.ndarray):
        emb = embedding / np.linalg.norm(embedding)
        if name in self._db:
            self._db[name] = np.vstack([self._db[name], emb])
        else:
            self._db[name] = emb[np.newaxis, :]
        self.save()
        n = len(self._db[name])
        print(f"Registrado '{name}' ({n} embedding{'s' if n > 1 else ''})")

    def query(self, embedding: np.ndarray, threshold: float) -> tuple[str, float]:
        if not self._db:
            return "Desconocido", 0.0
        emb = embedding / np.linalg.norm(embedding)
        best_name, best_sim = "Desconocido", 0.0
        for name, stored in self._db.items():
            # similitud coseno contra todos los embeddings del usuario → max
            sims = stored @ emb
            sim = float(sims.max())
            if sim > best_sim:
                best_sim, best_name = sim, name
        if best_sim >= threshold:
            return best_name, best_sim
        return "Desconocido", best_sim


# ── Utilidades ────────────────────────────────────────────────────────────────

def adaptive_threshold(z_m: float) -> float | None:
    """Umbral de similitud según distancia. None = no procesar."""
    if z_m <= 0 or z_m < DEPTH_MIN_MM / 1000:
        return None
    if z_m > DEPTH_MAX_MM / 1000:
        return None
    return THRESH_FAR if z_m > DIST_FAR_M else THRESH_NEAR


def liveness_check(face, depth_mm: np.ndarray, z_m: float) -> tuple[bool, float]:
    """Liveness por diferencia de profundidad entre nariz y ojos via landmarks.

    InsightFace kps = [ojo_izq, ojo_der, nariz, boca_izq, boca_der].
    En una cara 3D real la nariz está ~15-40 mm más cerca que los ojos.
    En una foto plana todos los puntos tienen la misma profundidad → diff ≈ 0.
    """
    if 0 < z_m < 0.4:
        return True, -1.0  # demasiado cerca — no penalizar

    if face.kps is None or len(face.kps) < 3:
        return True, -1.0

    def sample(pt, r=2):
        x, y = int(pt[0]), int(pt[1])
        patch = depth_mm[max(y-r,0):y+r+1, max(x-r,0):x+r+1]
        valid = patch[patch > 0]
        return float(np.median(valid)) if valid.size >= 4 else 0.0

    kps      = face.kps
    d_eye_l  = sample(kps[0])
    d_eye_r  = sample(kps[1])
    d_nose   = sample(kps[2])
    d_mouth_l = sample(kps[3])
    d_mouth_r = sample(kps[4])

    # Necesitamos al menos nariz y un ojo con dato válido
    refs = [d for d in [d_eye_l, d_eye_r, d_mouth_l, d_mouth_r] if d > 0]
    if not refs or d_nose == 0:
        return True, -1.0

    ref_mean = float(np.mean(refs))
    # nariz debe ser MÁS CERCANA (valor menor) que los puntos de referencia
    diff = ref_mean - d_nose   # positivo = nariz más cerca = cara real

    print(f"[liveness] z={z_m:.2f}m  nariz={d_nose:.0f}mm  "
          f"ref={ref_mean:.0f}mm  diff={diff:+.1f}mm  "
          f"→ {'LIVE' if diff >= LIVENESS_STD else 'FAIL'}")

    return diff >= LIVENESS_STD, diff


def face_depth_m(face, depth_mm: np.ndarray) -> float:
    """Mediana 5×5 px en el centro del bbox, en metros."""
    x1, y1, x2, y2 = face.bbox.astype(int)
    cx = int((x1 + x2) / 2)
    cy = int((y1 + y2) / 2)
    r = 2
    patch = depth_mm[max(cy-r,0):cy+r+1, max(cx-r,0):cx+r+1]
    valid = patch[patch > 0]
    return float(np.median(valid)) / 1000.0 if valid.size else 0.0


TRACK_MAX_AGE  = 3.0   # segundos sin ver el bbox antes de olvidar la identidad
TRACK_IOU_MIN  = 0.25  # IoU mínimo para considerar que es el mismo track

@dataclass
class FaceTrack:
    track_id:   int
    name:       str
    sim:        float
    bbox:       np.ndarray   # [x1,y1,x2,y2]
    last_seen:  float        # time.monotonic()


class FaceTracker:
    def __init__(self):
        self._tracks: list[FaceTrack] = []
        self._next_id = 0

    @staticmethod
    def _iou(a: np.ndarray, b: np.ndarray) -> float:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        if inter == 0:
            return 0.0
        union = (ax2-ax1)*(ay2-ay1) + (bx2-bx1)*(by2-by1) - inter
        return inter / union if union > 0 else 0.0

    def update(self, bbox: np.ndarray, name: str, sim: float) -> FaceTrack:
        """Asocia un bbox detectado a un track existente o crea uno nuevo.
        La identidad solo se actualiza si el nuevo reconocimiento es más confiable."""
        now = time.monotonic()
        best_track, best_iou = None, 0.0
        for t in self._tracks:
            iou = self._iou(bbox, t.bbox)
            if iou > best_iou:
                best_iou, best_track = iou, t

        if best_track is not None and best_iou >= TRACK_IOU_MIN:
            best_track.bbox      = bbox
            best_track.last_seen = now
            # Actualizar identidad solo si mejora o era desconocido
            if name != "Desconocido" and sim > best_track.sim:
                best_track.name = name
                best_track.sim  = sim
            return best_track
        else:
            track = FaceTrack(
                track_id  = self._next_id,
                name      = name,
                sim       = sim,
                bbox      = bbox,
                last_seen = now,
            )
            self._next_id += 1
            self._tracks.append(track)
            return track

    def purge(self):
        """Elimina tracks no vistos por más de TRACK_MAX_AGE segundos."""
        cutoff = time.monotonic() - TRACK_MAX_AGE
        self._tracks = [t for t in self._tracks if t.last_seen >= cutoff]


def build_face_analyzer():
    import onnxruntime as ort
    cuda_ok = "CUDAExecutionProvider" in ort.get_available_providers()
    providers = (["CUDAExecutionProvider", "CPUExecutionProvider"]
                 if cuda_ok else ["CPUExecutionProvider"])
    print(f"Face analyzer: {'CUDA' if cuda_ok else 'CPU'}")
    app = FaceAnalysis(name="buffalo_l", providers=providers)
    app.prepare(ctx_id=0 if cuda_ok else -1, det_size=(640, 640))
    return app


def decode_color(frame):
    w, h = frame.get_width(), frame.get_height()
    data = frame.get_data()
    fmt = frame.get_format()
    if fmt == OBFormat.RGB:
        return cv2.cvtColor(np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3)),
                            cv2.COLOR_RGB2BGR)
    if fmt == OBFormat.BGR:
        return np.frombuffer(data, dtype=np.uint8).reshape((h, w, 3))
    if fmt in (OBFormat.MJPG, OBFormat.H264, OBFormat.H265):
        return cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if fmt == OBFormat.YUYV:
        return cv2.cvtColor(np.frombuffer(data, dtype=np.uint8).reshape((h, w, 2)),
                            cv2.COLOR_YUV2BGR_YUYV)
    return None


def decode_depth(frame):
    w, h = frame.get_width(), frame.get_height()
    depth_mm = np.frombuffer(frame.get_data(), dtype=np.uint16).reshape((h, w)).astype(np.float32)
    depth_mm *= frame.get_depth_scale()
    depth_mm[(depth_mm < DEPTH_MIN_MM) | (depth_mm > DEPTH_MAX_MM)] = 0
    return depth_mm


def draw_face(img, face, track: "FaceTrack", z_m: float, is_live: bool):
    x1, y1, x2, y2 = face.bbox.astype(int)
    name, sim = track.name, track.sim

    if not is_live:
        color = (0, 165, 255)
    elif name == "Desconocido":
        color = (0, 0, 255)
    else:
        color = (0, 255, 0)

    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

    if face.kps is not None:
        for pt in face.kps.astype(int):
            cv2.circle(img, tuple(pt), 3, (0, 200, 255), -1)

    parts = [f"#{track.track_id}", name]
    if name != "Desconocido":
        parts.append(f"{sim:.2f}")
    if z_m > 0:
        parts.append(f"{z_m:.2f}m")
    if not is_live:
        parts.append("LIVENESS FAIL")

    label = "  ".join(parts)
    tw, th = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)[0]
    cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
    cv2.putText(img, label, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return img


def capture_loop(pipeline, frame_queue, stop_event):
    while not stop_event.is_set():
        frames = pipeline.wait_for_frames(100)
        if frames is None:
            continue
        if not frame_queue.empty():
            try:
                frame_queue.get_nowait()
            except queue.Empty:
                pass
        frame_queue.put(frames)


def main():
    print("Cargando modelo...")
    face_app = build_face_analyzer()
    db = FaceDB(DB_PATH)
    print("Listo. Teclas: 'r' registrar cara | 'q' salir")

    pipeline = Pipeline()
    config = Config()
    color_profiles = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
    config.enable_stream(color_profiles.get_default_video_stream_profile())
    depth_profiles = pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
    config.enable_stream(depth_profiles.get_default_video_stream_profile())
    config.set_align_mode(OBAlignMode.HW_MODE)
    pipeline.start(config)

    frame_queue = queue.Queue(maxsize=1)
    stop_event  = threading.Event()
    threading.Thread(target=capture_loop, args=(pipeline, frame_queue, stop_event),
                     daemon=True).start()

    tracker          = FaceTracker()
    pending_register = False

    try:
        while True:
            try:
                frames = frame_queue.get(timeout=0.5)
            except queue.Empty:
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                continue

            color_frame = frames.get_color_frame()
            depth_frame = frames.get_depth_frame()
            color_img = decode_color(color_frame) if color_frame else None
            if color_img is None:
                continue

            depth_mm = decode_depth(depth_frame) if depth_frame else None
            if depth_mm is not None:
                h, w = color_img.shape[:2]
                depth_mm = cv2.resize(depth_mm, (w, h), interpolation=cv2.INTER_NEAREST)

            faces = face_app.get(color_img)
            display = color_img.copy()
            tracker.purge()

            for face in faces:
                z_m = face_depth_m(face, depth_mm) if depth_mm is not None else 0.0

                if depth_mm is not None and z_m == 0.0:
                    is_live = False
                else:
                    is_live, _ = liveness_check(face, depth_mm, z_m) if depth_mm is not None else (True, -1.0)

                thresh = adaptive_threshold(z_m)

                if pending_register and face.embedding is not None:
                    name = input("Nombre para registrar: ").strip()
                    if name:
                        db.register(name, face.embedding)
                    pending_register = False

                # Reconocimiento solo si las condiciones lo permiten
                if is_live and thresh is not None and face.embedding is not None:
                    name, sim = db.query(face.embedding, thresh)
                else:
                    name, sim = "Desconocido", 0.0

                # El tracker decide si usar el nombre nuevo o conservar el anterior
                track = tracker.update(face.bbox.astype(int), name, sim)
                display = draw_face(display, face, track, z_m, is_live)

            cv2.imshow("Gemini 2 - Face Recognition", display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                pending_register = True
                print("Apunta a la cara y espera...")

    finally:
        stop_event.set()
        pipeline.stop()
        cv2.destroyAllWindows()
        print("Cámara detenida.")


if __name__ == "__main__":
    main()
