"""Nodo de reconocimiento facial con profundidad real (Orbbec Gemini 2).

Pipeline:
  [orbbec_camera_node] --camera/image_raw[/compressed]--> [orbbec_face_recognition_node] --vision/faces--> (JSON)
  [orbbec_camera_node] --camera/depth/image_raw----------^

Variante de ``vision_pkg/face_recognition_node.py`` que reemplaza la estimación
geométrica de distancia (modelo estenopeico) por una lectura directa del mapa de
profundidad publicado por ``orbbec_camera_node`` (``sensor_msgs/Image`` 16UC1, mm).
La distancia reportada en ``vision/faces`` pasa de ser una aproximación ±40% a
una medición real del sensor, lo que mejora la selección de hablante y el registro
de personas en escenas multi-persona.

- Se suscribe a la cámara. Por defecto consume JPEG comprimido
  (``sensor_msgs/CompressedImage`` en ``<input_topic>/compressed``); con
  ``use_compression:=false`` consume ``sensor_msgs/Image`` raw en ``<input_topic>``.
  El QoS es de sensor (BEST_EFFORT, depth=1): siempre procesa el frame más reciente
  y descarta los atrasados. La decodificación (costosa) ocurre en el hilo de
  inferencia a ``process_rate``, no en el callback a la tasa de la cámara.
- Detecta y genera embeddings de rostros con InsightFace (modelo ``buffalo_sc``).
- Consulta una base de datos PostgreSQL con los embeddings de las personas ya
  registradas y, por similitud de coseno, decide a quién corresponde cada rostro.
- Publica en ``vision/faces`` un String con un JSON. Si reconoce a la persona
  incluye su información; si no supera el umbral, el nombre es ``"desconocido"``.

El consumidor principal es la tool ``identificar_personas`` del agente Gemini
Live (reasoning_pkg): por eso ``publish_empty`` es True por defecto, para que
el último mensaje sea siempre reciente y el agente pueda distinguir "no hay
nadie" (num_rostros=0) de "el nodo de visión no está corriendo" (sin datos).

Registro de personas nuevas — servicio ``registrar_rostro`` (RegistrarRostro):
el agente lo invoca vía la tool ``registrar_persona`` cuando alguien desconocido
quiere darse de alta. Selección del rostro en escenas con varias personas:

1. Se descartan los rostros que ya matchean el catálogo (nunca se registra a
   un conocido con otro nombre, aunque esté más cerca).
2. Si queda un solo desconocido, es el candidato.
3. Si quedan varios, se aplica la heurística de hablante: el más cercano que
   mira a la cámara; si dos están a distancia demasiado parecida
   (``register_ambiguity_margin``), se devuelve un error descriptivo para que
   el agente pida cooperación ("que solo se acerque quien se registra").
4. Se acumulan hasta ``register_target_samples`` embeddings del candidato a lo
   largo de varios frames (verificando que es la misma persona) y se guarda la
   media normalizada. Antes de insertar se re-chequea contra el catálogo: si
   matchea a alguien con otro nombre, se rechaza ("ya registrado como X").

Tras un registro exitoso el embedding entra al catálogo en memoria al
instante: el siguiente frame ya reconoce a la persona.

IMPORTANTE: los embeddings de la BD deben haberse generado con el MISMO modelo
(``buffalo_sc`` -> w600k_mbf, 512-d). Un embedding de otro modelo con la misma
dimensión pasa los chequeos pero produce similitudes sin sentido.

La detección es costosa, así que se ejecuta en un hilo aparte a una tasa
configurable (``process_rate``), descartando los frames intermedios de la cámara.

Esquema de BD: dos tablas unidas por FK (configurable por parámetros)::

    CREATE TABLE registered_user (
        id     SERIAL PRIMARY KEY,
        nombre TEXT
    );
    CREATE TABLE user_embedding (
        id        SERIAL PRIMARY KEY,
        user_id   INTEGER REFERENCES registered_user(id),
        embedding REAL[]      -- o pgvector / bytea float32; se autodetecta
    );

Una persona puede tener varios embeddings (varias filas en user_embedding).

Dependencias pip (no son paquetes ROS)::

    pip install insightface onnxruntime psycopg2-binary numpy

Conexión a la BD: sale del ``.env`` de la raíz del workspace (igual que
``GOOGLE_API_KEY`` para el nodo Gemini). Variables: ``FACE_DB_HOST``,
``FACE_DB_PORT``, ``FACE_DB_NAME``, ``FACE_DB_USER``, ``FACE_DB_PASSWORD``,
o un DSN completo en ``DATABASE_URL``. Hay que exportarlas antes de lanzar
el nodo::

    set -a; source ~/mciav2_ws/.env; set +a
    ros2 run orbbec_vision_pkg orbbec_face_recognition_node

Los parámetros ROS ``db_*`` existen sólo como override puntual (vacíos por
defecto).

Parámetros ROS2
---------------
input_topic            (str,   'camera/image_raw'): base del topic de cámara
use_compression        (bool,  True)          : consume JPEG comprimido (debe
                                                coincidir con el camera_node)
output_topic           (str,   'vision/faces')
model_name             (str,   'buffalo_sc')  : pack de modelos InsightFace
det_size               (int,   320)           : tamaño del detector (cuadrado); 320
                                                es ~4x más rápido que 640
use_gpu                (bool,  False)          : usa CUDAExecutionProvider si True
similarity_threshold   (float, 0.5)           : umbral de coseno para reconocer
process_rate           (float, 1.0)           : Hz máximos de inferencia
publish_empty          (bool,  True)          : publicar aunque no haya rostros
inference_threads      (int,   2)             : núcleos para OpenMP/OpenCV (nota:
                                                onnxruntime >=1.10 ya no usa OpenMP)
db_dsn                 (str,   '')   : DSN completo; si vacío usa $DATABASE_URL
db_host                (str,   '')   : si vacío usa $FACE_DB_HOST ('localhost')
db_port                (int,   0)    : si 0 usa $FACE_DB_PORT (5432)
db_name                (str,   '')   : si vacío usa $FACE_DB_NAME
db_user                (str,   '')   : si vacío usa $FACE_DB_USER
db_password            (str,   '')   : si vacío usa $FACE_DB_PASSWORD
db_embedding_table     (str,   'user_embedding')
db_embedding_column    (str,   'embedding')
db_fk_column           (str,   'user_id')       : FK en user_embedding -> registered_user
db_user_table          (str,   'registered_user')
db_user_id_column      (str,   'id')            : PK de registered_user
db_name_column         (str,   'nombre')        : columna de nombre en registered_user
db_refresh_interval    (float, 60.0)          : cada cuánto recargar la BD (s)
register_service       (str,   'registrar_rostro')
register_attempts      (int,   10)            : frames máx. a inspeccionar por registro
register_interval      (float, 0.15)          : s de espera entre frames
register_target_samples (int,  5)             : embeddings a promediar (early stop)
register_min_samples   (int,   2)             : mínimo de muestras para aceptar
register_consistency   (float, 0.5)           : similitud mín. entre muestras (misma persona)
register_ambiguity_margin (float, 1.25)       : factor de distancia para decidir entre
                                                dos desconocidos sin ambigüedad
look_at_me_max_angle   (float, 20.0)          : |yaw|,|pitch| (grados) por debajo de
                                                los cuales se considera que la persona
                                                mira al robot (look_at_me=true)
look_at_me_asym_threshold (float, 0.15)       : fallback cuando el modelo no da pose:
                                                asimetría máx. de keypoints para frontal
real_face_width_m      (float, 0.16)          : ancho real medio de una cabeza (m),
                                                usado para estimar la distancia
camera_hfov_deg        (float, 60.0)          : FOV horizontal de la cámara (grados);
                                                se usa para derivar la focal si
                                                focal_length_px == 0
focal_length_px        (float, 0.0)           : focal en píxeles; si >0 sobre-escribe
                                                el cálculo por FOV
depth_topic            (str,   'camera/depth/image_raw') : topic del mapa de
                                                profundidad publicado por
                                                orbbec_camera_node
                                                (sensor_msgs/Image 16UC1, mm)
track_max_age          (float, 3.0)           : segundos sin actualización antes
                                                de eliminar un track
track_match_radius     (float, 0.4)           : radio en metros para asociar una
                                                detección a un track existente

Cada rostro publicado incluye además:
    look_at_me (bool)  : la persona está mirando al robot
    distance   (float) : distancia en metros medida por el sensor de profundidad
                         (fallback a estimación geométrica si depth no disponible)
El agente usa estos dos datos para inferir que quien le habla es la persona más
cercana que además le está mirando.
"""

import json
import math
import os
import threading
import time
from dataclasses import dataclass, field

# Los hilos OpenMP/ONNX spin-wait por defecto; PASSIVE los hace dormir
# entre inferencias y libera CPU a los demás servicios.
os.environ.setdefault('OMP_WAIT_POLICY', 'PASSIVE')

import cv2
import numpy as np
import rclpy
# cv_bridge incompatible con NumPy 2.x — no se usa en este nodo
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import String

from robot_interfaces.srv import RegistrarRostro

@dataclass
class FaceTrack:
    track_id: int
    user_id: int | None
    name: str
    last_pos: tuple  # (x_m, y_m, z_m)
    last_seen: float  # time.monotonic()
    similarity: float
    look_at_me: bool = False


try:
    from insightface.app import FaceAnalysis
except ImportError as e:
    raise SystemExit(
        'Falta InsightFace: pip install insightface onnxruntime'
    ) from e

try:
    import psycopg2
    from psycopg2 import sql
except ImportError as e:
    raise SystemExit(
        'Falta el driver de PostgreSQL: pip install psycopg2-binary'
    ) from e

# Una BD colgada (no caída) bloquea connect() el timeout TCP completo (~2 min)
# y congelaría el hilo de inferencia; con esto el fallo tarda 5 s como máximo.
DB_CONNECT_TIMEOUT_S = 5


class FaceRecognitionNode(Node):

    def __init__(self):
        super().__init__('orbbec_face_recognition_node')

        # --- Parámetros ---
        self.declare_parameter('input_topic', 'camera/image_raw')
        self.declare_parameter('use_compression', True)
        self.declare_parameter('output_topic', 'vision/faces')
        self.declare_parameter('model_name', 'buffalo_sc')
        self.declare_parameter('det_size', 320)
        self.declare_parameter('use_gpu', False)
        self.declare_parameter('similarity_threshold', 0.5)
        self.declare_parameter('process_rate', 1.0)
        self.declare_parameter('publish_empty', True)
        self.declare_parameter('inference_threads', 2)

        # Conexión: por defecto sale del .env del workspace (FACE_DB_* /
        # DATABASE_URL); los parámetros sólo sirven como override puntual.
        self.declare_parameter('db_dsn', '')
        self.declare_parameter('db_host', '')
        self.declare_parameter('db_port', 0)
        self.declare_parameter('db_name', '')
        self.declare_parameter('db_user', '')
        self.declare_parameter('db_password', '')
        # Esquema de dos tablas: user_embedding (FK user_id) -> registered_user (id, nombre)
        self.declare_parameter('db_embedding_table', 'user_embedding')
        self.declare_parameter('db_embedding_column', 'embedding')
        self.declare_parameter('db_fk_column', 'user_id')        # FK en user_embedding
        self.declare_parameter('db_user_table', 'registered_user')
        self.declare_parameter('db_user_id_column', 'id')         # PK de registered_user
        self.declare_parameter('db_name_column', 'nombre')        # nombre en registered_user
        self.declare_parameter('db_refresh_interval', 60.0)

        # Registro de personas nuevas (servicio)
        self.declare_parameter('register_service', 'registrar_rostro')
        self.declare_parameter('register_attempts', 10)      # frames máx. a inspeccionar
        self.declare_parameter('register_interval', 0.15)    # s entre frames
        self.declare_parameter('register_target_samples', 5) # embeddings a promediar
        self.declare_parameter('register_min_samples', 2)    # mínimo para aceptar
        # Dos embeddings consecutivos de la misma persona suelen superar 0.6;
        # por debajo de esto se asume que el candidato cambió de identidad.
        self.declare_parameter('register_consistency', 0.5)
        # Margen de distancia para considerar "claramente más cercano" a un
        # desconocido frente a otro (1.25 = 25% más cerca).
        self.declare_parameter('register_ambiguity_margin', 1.25)

        # Atención (look_at_me) y distancia aproximada
        self.declare_parameter('look_at_me_max_angle', 20.0)
        self.declare_parameter('look_at_me_asym_threshold', 0.15)
        self.declare_parameter('real_face_width_m', 0.16)
        self.declare_parameter('camera_hfov_deg', 60.0)
        self.declare_parameter('focal_length_px', 0.0)

        # Profundidad real del sensor Orbbec
        self.declare_parameter('depth_topic', 'camera/depth/image_raw')
        # Intrínsecos de la cámara (para cálculo 3D)
        self.declare_parameter('camera_info_topic', 'camera/image_raw/camera_info')

        # Actividad 1 — Umbral adaptativo por distancia
        self.declare_parameter('dist_min', 0.4)          # m: por debajo → skip
        self.declare_parameter('dist_max', 3.5)          # m: por encima → skip
        self.declare_parameter('dist_far_threshold', 2.5) # m: a partir de aquí sube el umbral
        self.declare_parameter('threshold_far', 0.58)    # umbral para zona lejana

        # Actividad 2 — Liveness detection (anti-spoofing)
        self.declare_parameter('liveness_check', True)
        self.declare_parameter('liveness_depth_std_min', 15.0)  # mm: std mínimo para cara real
        self.declare_parameter('liveness_bbox_expand', -0.2)    # factor: negativo = contraer bbox (solo zona interior de la cara)

        # Actividad 3 — Tracking 3D entre frames
        self.declare_parameter('track_max_age', 3.0)      # s sin actualización antes de eliminar el track
        self.declare_parameter('track_match_radius', 0.4)  # m: radio de asociación cara → track
        # Umbral de confirmación contextual: cuando el track ya tiene identidad conocida y el
        # mejor match del embedding es esa misma persona, basta con este umbral más bajo para
        # confirmar (la posición 3D ya aporta evidencia de identidad).
        self.declare_parameter('track_confirm_threshold', 0.30)

        input_topic = self.get_parameter('input_topic').value
        self._use_compression = bool(self.get_parameter('use_compression').value)
        output_topic = self.get_parameter('output_topic').value
        model_name = self.get_parameter('model_name').value
        det = int(self.get_parameter('det_size').value)
        use_gpu = bool(self.get_parameter('use_gpu').value)
        self._threshold = float(self.get_parameter('similarity_threshold').value)
        process_rate = max(0.1, float(self.get_parameter('process_rate').value))
        self._publish_empty = bool(self.get_parameter('publish_empty').value)
        inference_threads = max(1, int(self.get_parameter('inference_threads').value))
        # Limita los hilos OpenMP (afecta a OpenCV y a builds antiguos de ORT;
        # onnxruntime >=1.10 gestiona sus hilos por sesión y lo ignora).
        os.environ['OMP_NUM_THREADS'] = str(inference_threads)
        cv2.setNumThreads(inference_threads)
        self._db_refresh_interval = float(
            self.get_parameter('db_refresh_interval').value
        )
        self._look_angle = float(self.get_parameter('look_at_me_max_angle').value)
        self._look_asym_threshold = float(
            self.get_parameter('look_at_me_asym_threshold').value
        )
        self._real_face_width_m = float(self.get_parameter('real_face_width_m').value)
        self._hfov_deg = float(self.get_parameter('camera_hfov_deg').value)
        self._focal_px = float(self.get_parameter('focal_length_px').value)

        # Estado para profundidad real
        self._latest_depth: np.ndarray | None = None  # uint16 HxW mm, alineado al color
        self._depth_lock = threading.Lock()
        self._last_frame_width = 640  # fallback para _estimate_distance cuando no hay depth

        # Actividad 1 — Umbral adaptativo
        self._dist_min          = float(self.get_parameter('dist_min').value)
        self._dist_max          = float(self.get_parameter('dist_max').value)
        self._dist_far_thr      = float(self.get_parameter('dist_far_threshold').value)
        self._threshold_far     = float(self.get_parameter('threshold_far').value)

        # Actividad 2 — Liveness
        self._liveness_enabled  = bool(self.get_parameter('liveness_check').value)
        self._liveness_std_min  = float(self.get_parameter('liveness_depth_std_min').value)
        self._liveness_expand   = float(self.get_parameter('liveness_bbox_expand').value)

        # Actividad 3 — Tracking 3D
        self._track_max_age      = float(self.get_parameter('track_max_age').value)
        self._track_match_radius = float(self.get_parameter('track_match_radius').value)
        self._track_confirm_thr  = float(self.get_parameter('track_confirm_threshold').value)
        self._tracks: dict[int, FaceTrack] = {}
        self._next_track_id = 0

        self._reg_attempts = max(1, int(self.get_parameter('register_attempts').value))
        self._reg_interval = max(0.0, float(self.get_parameter('register_interval').value))
        self._reg_target = max(1, int(self.get_parameter('register_target_samples').value))
        self._reg_min = max(1, int(self.get_parameter('register_min_samples').value))
        self._reg_consistency = float(self.get_parameter('register_consistency').value)
        self._reg_margin = float(self.get_parameter('register_ambiguity_margin').value)

        # --- InsightFace ---
        providers = (
            ['CUDAExecutionProvider', 'CPUExecutionProvider']
            if use_gpu else ['CPUExecutionProvider']
        )
        self.get_logger().info(
            f'Cargando InsightFace "{model_name}" (providers={providers})...'
        )
        self._app = FaceAnalysis(name=model_name, providers=providers)
        self._app.prepare(ctx_id=0 if use_gpu else -1, det_size=(det, det))
        # Serializa el acceso al modelo entre el hilo de inferencia (1 Hz) y el
        # callback del servicio de registro.
        self._model_lock = threading.Lock()
        self.get_logger().info('InsightFace listo.')

        # --- Base de datos: catálogo de embeddings en memoria ---
        self._db_lock = threading.Lock()
        self._ids: list = []
        self._names: list = []
        self._matrix = np.zeros((0, 0), dtype=np.float32)  # (N, dim) normalizada
        self._last_db_load = 0.0
        self._load_database()

        # --- ROS interfaces ---
        self._bridge = None  # cv_bridge no usada (incompatible NumPy 2.x)
        self._pub = self.create_publisher(String, output_topic, 10)
        # QoS de sensor: best-effort + depth=1 para casar con el camera_node y
        # quedarnos siempre con el frame más reciente, descartando los atrasados.
        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        # Suscripción y servicio en grupos distintos: con MultiThreadedExecutor
        # siguen llegando frames frescos mientras el registro está en curso.
        self._sub_group = MutuallyExclusiveCallbackGroup()
        self._srv_group = MutuallyExclusiveCallbackGroup()

        if self._use_compression:
            sub_topic = f'{input_topic}/compressed'
            self._sub = self.create_subscription(
                CompressedImage, sub_topic, self._on_image, sensor_qos,
                callback_group=self._sub_group,
            )
        else:
            sub_topic = input_topic
            self._sub = self.create_subscription(
                Image, sub_topic, self._on_image, sensor_qos,
                callback_group=self._sub_group,
            )

        depth_topic_name = self.get_parameter('depth_topic').value
        self._depth_sub = self.create_subscription(
            Image, depth_topic_name,
            self._on_depth, sensor_qos,
            callback_group=self._sub_group,
        )

        # CameraInfo — transient_local para recibir el mensaje aunque lleguemos tarde
        from rclpy.qos import QoSDurabilityPolicy
        self._intrinsics: tuple | None = None  # (fx, fy, cx, cy)
        latched_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        camera_info_topic = self.get_parameter('camera_info_topic').value
        self.create_subscription(
            CameraInfo, camera_info_topic, self._on_camera_info, latched_qos,
        )

        self._srv = self.create_service(
            RegistrarRostro, self.get_parameter('register_service').value,
            self._on_registrar, callback_group=self._srv_group,
        )

        # --- Frame compartido + hilo de inferencia ---
        self._frame_lock = threading.Lock()
        self._latest = None  # último msg de cámara sin decodificar (Compressed/Image)
        self._last_processed = None  # último msg ya inferido por el worker
        self._period = 1.0 / process_rate
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

        self.get_logger().info(
            f'OrbbecFaceRecognitionNode listo | in="{sub_topic}" out="{output_topic}" '
            f'| depth="{depth_topic_name}" | {len(self._ids)} embeddings '
            f'| umbral={self._threshold} | {process_rate} Hz | det={det} '
            f'| inference_threads={inference_threads}'
        )

    # ------------------------------------------------------------------
    # Base de datos
    # ------------------------------------------------------------------
    def _param_or_env(self, param, env_var, default=''):
        """Parámetro ROS si fue fijado; si no, la variable de entorno (.env)."""
        value = self.get_parameter(param).value
        return value if value else os.environ.get(env_var, default)

    def _connect(self):
        dsn = self.get_parameter('db_dsn').value or os.environ.get('DATABASE_URL', '')
        if dsn:
            return psycopg2.connect(dsn, connect_timeout=DB_CONNECT_TIMEOUT_S)
        return psycopg2.connect(
            host=self._param_or_env('db_host', 'FACE_DB_HOST', 'localhost'),
            port=int(self._param_or_env('db_port', 'FACE_DB_PORT', '5432') or 5432),
            dbname=self._param_or_env('db_name', 'FACE_DB_NAME'),
            user=self._param_or_env('db_user', 'FACE_DB_USER'),
            password=self._param_or_env('db_password', 'FACE_DB_PASSWORD'),
            connect_timeout=DB_CONNECT_TIMEOUT_S,
        )

    @staticmethod
    def _parse_embedding(raw):
        """Convierte el valor crudo de la BD en un vector float32 normalizado."""
        if raw is None:
            return None
        if isinstance(raw, (bytes, bytearray, memoryview)):
            vec = np.frombuffer(bytes(raw), dtype=np.float32)
        elif isinstance(raw, str):
            # Formato pgvector '[...]' o array de Postgres '{...}'
            s = raw.strip().strip('[]{}')
            if not s:
                return None
            vec = np.array([float(x) for x in s.split(',')], dtype=np.float32)
        else:
            # list / tuple (REAL[] o float8[])
            vec = np.asarray(raw, dtype=np.float32)

        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else None

    def _load_database(self):
        """Lee los embeddings uniendo user_embedding con registered_user.

        Una persona puede tener varios embeddings; cada fila queda asociada a
        su (id, nombre) de usuario para que el reconocimiento elija el mejor.
        """
        # sql.Identifier cita los nombres de tabla/columna: un parámetro
        # malformado falla en vez de inyectarse en la query.
        query = sql.SQL(
            'SELECT u.{uid}, u.{name}, e.{emb} FROM {emb_table} e '
            'JOIN {user_table} u ON e.{fk} = u.{uid}'
        ).format(
            uid=sql.Identifier(self.get_parameter('db_user_id_column').value),
            name=sql.Identifier(self.get_parameter('db_name_column').value),
            emb=sql.Identifier(self.get_parameter('db_embedding_column').value),
            emb_table=sql.Identifier(self.get_parameter('db_embedding_table').value),
            user_table=sql.Identifier(self.get_parameter('db_user_table').value),
            fk=sql.Identifier(self.get_parameter('db_fk_column').value),
        )

        ids, names, vectors = [], [], []
        try:
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(query)
                    rows = cur.fetchall()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - cualquier fallo de BD no debe tumbar el nodo
            self.get_logger().error(f'No se pudo consultar la BD: {exc}')
            self._last_db_load = time.monotonic()
            return

        dim = None
        for row_id, name, raw_emb in rows:
            vec = self._parse_embedding(raw_emb)
            if vec is None:
                continue
            if dim is None:
                dim = vec.shape[0]
            elif vec.shape[0] != dim:
                self.get_logger().warn(
                    f'Embedding de "{name}" (id={row_id}) con dimensión '
                    f'{vec.shape[0]} != {dim}; se omite.'
                )
                continue
            ids.append(row_id)
            names.append(name)
            vectors.append(vec)

        matrix = (
            np.vstack(vectors).astype(np.float32)
            if vectors else np.zeros((0, dim or 0), dtype=np.float32)
        )
        with self._db_lock:
            self._ids, self._names, self._matrix = ids, names, matrix
        self._last_db_load = time.monotonic()
        self.get_logger().info(f'BD recargada: {len(ids)} embeddings.')

    # ------------------------------------------------------------------
    # Atención y distancia
    # ------------------------------------------------------------------
    def _looks_at_camera(self, face):
        """True si la persona mira al robot (frontal).

        Usa ``face.pose`` (yaw, pitch) si el modelo lo provee; si no, aproxima
        con la simetría horizontal de los 5 keypoints (ojos/nariz). Nota:
        ``buffalo_sc`` no incluye modelo de pose, así que con el pack por
        defecto siempre se usa el fallback (que sólo capta yaw, no pitch).
        """
        pose = getattr(face, 'pose', None)
        if pose is not None:
            yaw, pitch = float(pose[0]), float(pose[1])
            return bool(abs(yaw) <= self._look_angle and abs(pitch) <= self._look_angle)

        kps = getattr(face, 'kps', None)
        if kps is not None and len(kps) >= 3:
            # kps[0]=ojo_izq, kps[1]=ojo_der, kps[2]=nariz
            left_x, right_x, nose_x = float(kps[0][0]), float(kps[1][0]), float(kps[2][0])
            span = right_x - left_x
            if span <= 0:
                return False
            asym = abs((nose_x - left_x) - (right_x - nose_x)) / span
            return bool(asym <= self._look_asym_threshold)
        return False

    def _estimate_distance(self, face, frame_width):
        """Distancia aproximada en metros (modelo estenopeico).

        distancia = (ancho_real_cara · focal_px) / ancho_cara_px

        La focal se toma de ``focal_length_px`` si está fijada (>0); si no, se deriva
        del FOV horizontal. Es una estimación: sirve para comparar quién está más
        cerca/lejos, no como medición métrica exacta.
        """
        x1, _, x2, _ = face.bbox
        w_px = max(1.0, float(x2 - x1))
        focal_px = self._focal_px
        if focal_px <= 0.0:
            focal_px = (frame_width / 2.0) / math.tan(math.radians(self._hfov_deg) / 2.0)
        dist = (self._real_face_width_m * focal_px) / w_px
        return round(dist, 2)

    def _adaptive_threshold(self, z_m: float) -> float | None:
        """Umbral de similitud según distancia. None = skip (fuera de rango útil)."""
        if z_m <= 0 or z_m < self._dist_min or z_m > self._dist_max:
            return None
        if z_m > self._dist_far_thr:
            return self._threshold_far
        return self._threshold

    def _liveness_check(self, face, depth: np.ndarray) -> tuple[bool, float]:
        """Detecta si el rostro es real (cara 3D) o plano (foto/pantalla).

        Usa la desviación estándar del depth en el bbox: una cara real tiene
        variación ≥ liveness_depth_std_min mm; una foto/pantalla es casi plana.
        Devuelve (es_real, std_mm). Si no hay suficientes píxeles válidos,
        asume real para no bloquear en condiciones de depth parcial.
        """
        x1, y1, x2, y2 = (int(v) for v in face.bbox)
        w, h = x2 - x1, y2 - y1
        ex = self._liveness_expand
        x1e = max(0, int(x1 - w * ex))
        y1e = max(0, int(y1 - h * ex))
        x2e = min(depth.shape[1], int(x2 + w * ex))
        y2e = min(depth.shape[0], int(y2 + h * ex))
        roi = depth[y1e:y2e, x1e:x2e]
        valid = roi[roi > 0]
        if valid.size < 10:
            return True, 0.0  # depth insuficiente → no bloquear
        std = float(np.std(valid))
        return std >= self._liveness_std_min, round(std, 1)

    def _match_track(self, x_m: float, y_m: float, z_m: float, now: float) -> 'FaceTrack | None':
        """Devuelve el track activo más cercano en 3D dentro del radio de matching, o None."""
        best: FaceTrack | None = None
        best_dist = self._track_match_radius
        for track in self._tracks.values():
            if now - track.last_seen >= self._track_max_age:
                continue
            dx = x_m - track.last_pos[0]
            dy = y_m - track.last_pos[1]
            dz = z_m - track.last_pos[2]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if dist < best_dist:
                best_dist = dist
                best = track
        return best

    def _depth_distance(self, face) -> float:
        """Distancia en metros usando el mapa de profundidad real del sensor Orbbec.

        Toma la mediana de una ventana 5×5 alrededor del centro del bounding box,
        ignorando píxeles con valor 0 (dato inválido del sensor). Si no hay datos
        de profundidad disponibles o todos son inválidos en la ROI, cae de vuelta
        al modelo estenopeico (_estimate_distance).
        """
        with self._depth_lock:
            depth = self._latest_depth
        if depth is None:
            return self._estimate_distance(face, self._last_frame_width)
        x1, y1, x2, y2 = (int(v) for v in face.bbox)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        h, w = depth.shape
        r = 2  # ventana 5×5
        roi = depth[max(0, cy - r):min(h, cy + r + 1),
                    max(0, cx - r):min(w, cx + r + 1)]
        valid = roi[roi > 0]
        if valid.size == 0:
            return self._estimate_distance(face, w)
        return round(float(np.median(valid)) / 1000.0, 2)  # mm → metros

    # ------------------------------------------------------------------
    # ROS callbacks / hilo de inferencia
    # ------------------------------------------------------------------
    def _on_image(self, msg):
        # Sólo guardamos el mensaje crudo; la decodificación (costosa) se hace en
        # el hilo de inferencia a process_rate, no aquí a la tasa de la cámara.
        with self._frame_lock:
            self._latest = msg

    def _on_camera_info(self, msg: CameraInfo):
        k = msg.k  # array de 9 elementos: [fx, 0, cx, 0, fy, cy, 0, 0, 1]
        self._intrinsics = (k[0], k[4], k[2], k[5])  # fx, fy, cx, cy
        self.get_logger().info(
            f'Intrínsecos recibidos: fx={k[0]:.3f} fy={k[4]:.3f} cx={k[2]:.3f} cy={k[5]:.3f}',
            once=True,
        )

    def _pixel_to_3d(self, cx_px: float, cy_px: float, z_m: float) -> tuple[float, float]:
        """Convierte centro de bbox + profundidad a (x_m, y_m) en espacio de cámara.

        x_m positivo = derecha, y_m positivo = abajo, z_m = profundidad (ya calculada).
        Devuelve (0.0, 0.0) si los intrínsecos no están disponibles o z_m <= 0.
        """
        if self._intrinsics is None or z_m <= 0:
            return 0.0, 0.0
        fx, fy, cx, cy = self._intrinsics
        x_m = round((cx_px - cx) * z_m / fx, 3)
        y_m = round((cy_px - cy) * z_m / fy, 3)
        return x_m, y_m

    def _on_depth(self, msg: Image):
        # Parseo manual para evitar cv_bridge (incompatible con NumPy 2.x en Jazzy).
        # El mensaje llega como 16UC1 (uint16, mm) construido sin cv_bridge en el nodo
        # de cámara, así que el parsing directo es seguro.
        try:
            depth = np.frombuffer(bytes(msg.data), dtype=np.uint16).reshape(
                (msg.height, msg.width)
            )
        except Exception:
            return
        with self._depth_lock:
            self._latest_depth = depth.copy()

    def _loop(self):
        while not self._stop.is_set():
            start = time.monotonic()
            try:
                self._process_once()
            except Exception as exc:  # noqa: BLE001
                self.get_logger().error(f'Error procesando frame: {exc}')
            self._stop.wait(max(0.0, self._period - (time.monotonic() - start)))

    def _decode(self, msg):
        """Decodifica un msg de cámara a BGR; None si falla."""
        try:
            buf = np.frombuffer(msg.data, dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception as exc:  # noqa: BLE001
            self.get_logger().warn(f'No se pudo decodificar la imagen: {exc}')
            return None

    def _process_once(self):
        if (time.monotonic() - self._last_db_load) >= self._db_refresh_interval:
            self._load_database()

        # No consumimos el frame (el servicio de registro también lo lee);
        # recordamos cuál fue el último inferido para no repetirlo.
        with self._frame_lock:
            msg = self._latest
        if msg is None or msg is self._last_processed:
            return
        self._last_processed = msg

        frame = self._decode(msg)
        if frame is None:
            return
        stamp, frame_id = msg.header.stamp, msg.header.frame_id
        now = time.monotonic()

        with self._model_lock:
            faces = self._app.get(frame)

        # Con tracking, puede haber algo que publicar aunque no se detecten caras
        active_tracks = any(
            now - t.last_seen < self._track_max_age for t in self._tracks.values()
        ) if hasattr(self, '_tracks') else False
        if not faces and not self._publish_empty and not active_tracks:
            return

        with self._db_lock:
            ids, names, matrix = self._ids, self._names, self._matrix

        frame_width = frame.shape[1]
        self._last_frame_width = frame_width
        with self._depth_lock:
            depth_snap = self._latest_depth  # snapshot para liveness (mismo depth que _depth_distance)

        rostros = []
        matched_track_ids: set[int] = set()

        for face in faces:
            x1, y1, x2, y2 = (int(v) for v in face.bbox)
            cx_px = (x1 + x2) / 2.0
            cy_px = (y1 + y2) / 2.0
            z_m = self._depth_distance(face)
            x_m, y_m = self._pixel_to_3d(cx_px, cy_px, z_m)

            # Actividad 1 — umbral adaptativo: skip si fuera del rango útil
            thr = self._adaptive_threshold(z_m)
            if thr is None:
                rostros.append({
                    'id': None, 'nombre': 'desconocido', 'reconocido': False,
                    'similitud': 0.0, 'bbox': [x1, y1, x2, y2],
                    'look_at_me': self._looks_at_camera(face),
                    'distance': z_m, 'x_m': x_m, 'y_m': y_m, 'z_m': z_m,
                    'liveness': None, 'liveness_std': None,
                    'threshold_used': None, 'skip_reason': 'distancia_fuera_de_rango',
                    'track_id': None, 'track_age': None, 'from_tracker': False,
                })
                continue

            # Actividad 2 — liveness detection
            is_live, live_std = True, 0.0
            if self._liveness_enabled and depth_snap is not None:
                is_live, live_std = self._liveness_check(face, depth_snap)
            if not is_live:
                self.get_logger().debug(
                    f'Liveness FAIL en ({int(cx_px)},{int(cy_px)}) std={live_std:.1f} mm — posible foto'
                )
                rostros.append({
                    'id': None, 'nombre': 'desconocido', 'reconocido': False,
                    'similitud': 0.0, 'bbox': [x1, y1, x2, y2],
                    'look_at_me': self._looks_at_camera(face),
                    'distance': z_m, 'x_m': x_m, 'y_m': y_m, 'z_m': z_m,
                    'liveness': False, 'liveness_std': live_std,
                    'threshold_used': thr, 'skip_reason': 'liveness_fail',
                    'track_id': None, 'track_age': None, 'from_tracker': False,
                })
                continue

            emb = face.normed_embedding  # ya viene L2-normalizado
            look = self._looks_at_camera(face)

            # Actividad 3 — asociar a track existente antes del reconocimiento,
            # para poder aplicar umbral contextual si el track tiene identidad.
            track = self._match_track(x_m, y_m, z_m, now)

            row_id, name, sim = None, 'desconocido', 0.0
            mejor_candidato, mejor_sim_raw = None, 0.0  # mejor match incluso si no supera umbral
            if matrix.shape[0] > 0:
                sims = matrix @ emb
                best = int(np.argmax(sims))
                sim = float(sims[best])
                mejor_candidato = names[best]
                mejor_sim_raw = round(sim, 4)
                # Umbral efectivo: si el track ya conoce a esta persona y el mejor
                # match es el mismo individuo, basta con track_confirm_threshold.
                effective_thr = thr
                if (track is not None
                        and track.name != 'desconocido'
                        and names[best] == track.name
                        and sim >= self._track_confirm_thr):
                    effective_thr = self._track_confirm_thr
                if sim >= effective_thr:
                    row_id, name = ids[best], names[best]

            from_tracker = False
            if track is None:
                track = FaceTrack(
                    track_id=self._next_track_id,
                    user_id=row_id, name=name,
                    last_pos=(x_m, y_m, z_m),
                    last_seen=now, similarity=sim,
                    look_at_me=look,
                )
                self._next_track_id += 1
                self._tracks[track.track_id] = track
                matched_track_ids.add(track.track_id)  # evita doble-publicación en el loop de extensión
                track_age = 0.0
            else:
                matched_track_ids.add(track.track_id)
                track_age = now - track.last_seen
                # Si la detección actual no reconoce pero el track tiene identidad → usarla
                if name == 'desconocido' and track.name != 'desconocido':
                    name = track.name
                    row_id = track.user_id
                    sim = track.similarity
                    from_tracker = True
                # Actualizar track: solo con identidad exitosa (nunca sobrescribir con 'desconocido')
                track.last_pos = (x_m, y_m, z_m)
                track.last_seen = now
                track.look_at_me = look
                if not from_tracker and name != 'desconocido' and sim > track.similarity:
                    track.name = name
                    track.user_id = row_id
                    track.similarity = sim

            rostros.append({
                'id': row_id,
                'nombre': name,
                'reconocido': name != 'desconocido',
                'similitud': round(sim, 4),
                'mejor_candidato': mejor_candidato if name == 'desconocido' else None,
                'mejor_sim_raw': mejor_sim_raw if name == 'desconocido' else None,
                'bbox': [x1, y1, x2, y2],
                'look_at_me': look,
                'distance': z_m,
                'x_m': x_m,
                'y_m': y_m,
                'z_m': z_m,
                'liveness': is_live,
                'liveness_std': live_std,
                'threshold_used': round(thr, 3),
                'skip_reason': None,
                'track_id': track.track_id,
                'track_age': round(track_age, 2),
                'from_tracker': from_tracker,
            })

        # Actividad 3 — extender identidad de tracks no detectados en este frame
        for track in list(self._tracks.values()):
            if track.track_id in matched_track_ids:
                continue
            age = now - track.last_seen
            if age >= self._track_max_age:
                continue
            rostros.append({
                'id': track.user_id,
                'nombre': track.name,
                'reconocido': track.name != 'desconocido',
                'similitud': round(track.similarity, 4),
                'bbox': None,
                'look_at_me': track.look_at_me,
                'distance': track.last_pos[2],
                'x_m': track.last_pos[0],
                'y_m': track.last_pos[1],
                'z_m': track.last_pos[2],
                'liveness': None,
                'liveness_std': None,
                'threshold_used': None,
                'skip_reason': None,
                'track_id': track.track_id,
                'track_age': round(age, 2),
                'from_tracker': True,
            })

        # Eliminar tracks expirados
        for tid in [tid for tid, t in self._tracks.items()
                    if now - t.last_seen >= self._track_max_age]:
            del self._tracks[tid]

        payload = {
            'stamp': stamp.sec + stamp.nanosec * 1e-9,
            'frame_id': frame_id,
            'num_rostros': len(rostros),
            'rostros': rostros,
        }
        out = String()
        out.data = json.dumps(payload, ensure_ascii=False)
        self._pub.publish(out)

    # ------------------------------------------------------------------
    # Registro de personas nuevas (servicio registrar_rostro)
    # ------------------------------------------------------------------
    def _classify_faces(self, faces):
        """Separa los rostros en (desconocidos, mejor_conocido).

        ``mejor_conocido`` es ``(similitud, nombre)`` del rostro con mejor match
        en el catálogo, o None si ninguno supera el umbral.
        """
        with self._db_lock:
            names, matrix = self._names, self._matrix
        unknowns, best_known = [], None
        for face in faces:
            sim, name = 0.0, None
            if matrix.shape[0] > 0:
                sims = matrix @ face.normed_embedding
                best = int(np.argmax(sims))
                sim, name = float(sims[best]), names[best]
            if sim >= self._threshold:
                if best_known is None or sim > best_known[0]:
                    best_known = (sim, name)
            else:
                unknowns.append(face)
        return unknowns, best_known

    def _pick_unknown(self, unknowns, frame_width):
        """Elige a quién registrar entre varios desconocidos.

        Misma heurística de hablante que usa el agente: el más cercano que
        mira a la cámara. Devuelve ``(face, ambiguo)``: ambiguo=True cuando
        hay dos candidatos a distancias demasiado parecidas para decidir.
        """
        if len(unknowns) == 1:
            return unknowns[0], False
        pool = [f for f in unknowns if self._looks_at_camera(f)] or unknowns
        pool = sorted(pool, key=lambda f: self._depth_distance(f))
        if len(pool) == 1:
            return pool[0], False
        d0 = self._depth_distance(pool[0])
        d1 = self._depth_distance(pool[1])
        return pool[0], d1 < d0 * self._reg_margin

    def _get_or_create_user(self, conn, nombre):
        """Devuelve el id del usuario; lo crea si no existe."""
        table = sql.Identifier(self.get_parameter('db_user_table').value)
        id_col = sql.Identifier(self.get_parameter('db_user_id_column').value)
        name_col = sql.Identifier(self.get_parameter('db_name_column').value)
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL('SELECT {id} FROM {t} WHERE {n} = %s').format(
                    id=id_col, t=table, n=name_col),
                (nombre,),
            )
            row = cur.fetchone()
            if row:
                return int(row[0])
            cur.execute(
                sql.SQL('INSERT INTO {t} ({n}) VALUES (%s) RETURNING {id}').format(
                    t=table, n=name_col, id=id_col),
                (nombre,),
            )
            user_id = int(cur.fetchone()[0])
        conn.commit()
        self.get_logger().info(f'Usuario nuevo creado: "{nombre}" (id={user_id})')
        return user_id

    def _save_embedding(self, conn, user_id, embedding):
        """Inserta el embedding (columna real[]) y devuelve su id."""
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL('INSERT INTO {t} ({fk}, {e}) VALUES (%s, %s) RETURNING id').format(
                    t=sql.Identifier(self.get_parameter('db_embedding_table').value),
                    fk=sql.Identifier(self.get_parameter('db_fk_column').value),
                    e=sql.Identifier(self.get_parameter('db_embedding_column').value)),
                (user_id, np.asarray(embedding, dtype=np.float32).tolist()),
            )
            emb_id = int(cur.fetchone()[0])
        conn.commit()
        return emb_id

    def _append_to_catalog(self, user_id, nombre, emb):
        """Añade el embedding al catálogo en memoria: reconocimiento inmediato,
        sin esperar el refresh periódico de la BD."""
        emb = np.asarray(emb, dtype=np.float32)[None, :]
        with self._db_lock:
            # Copias nuevas (no mutar): el hilo de inferencia puede estar
            # usando las referencias actuales fuera del lock.
            self._ids = self._ids + [user_id]
            self._names = self._names + [nombre]
            self._matrix = (
                emb if self._matrix.shape[0] == 0
                else np.vstack([self._matrix, emb])
            )

    def _capture_unknown(self):
        """Observa la escena y devuelve el embedding del desconocido a registrar.

        Inspecciona hasta ``register_attempts`` frames y acumula embeddings del
        candidato (verificando entre frames que sigue siendo la misma persona);
        el resultado es la media normalizada, más estable que un solo disparo.

        Devuelve ``(embedding|None, info: dict)`` con contadores para que el
        servicio construya un error explicativo si falla.
        """
        samples = []
        info = {'frames': 0, 'con_caras': 0, 'ambiguos': 0,
                'max_desconocidos': 0, 'conocido': None}
        last_msg = None

        for _ in range(self._reg_attempts):
            with self._frame_lock:
                msg = self._latest
            if msg is None or msg is last_msg:
                time.sleep(self._reg_interval)
                continue
            last_msg = msg
            frame = self._decode(msg)
            if frame is None:
                continue
            info['frames'] += 1

            with self._model_lock:
                faces = self._app.get(frame)
            if not faces:
                time.sleep(self._reg_interval)
                continue
            info['con_caras'] += 1

            unknowns, best_known = self._classify_faces(faces)
            if best_known and (info['conocido'] is None
                               or best_known[0] > info['conocido'][0]):
                info['conocido'] = best_known
            if not unknowns:
                time.sleep(self._reg_interval)
                continue
            info['max_desconocidos'] = max(info['max_desconocidos'], len(unknowns))

            face, ambiguous = self._pick_unknown(unknowns, frame.shape[1])
            if ambiguous:
                info['ambiguos'] += 1
                time.sleep(self._reg_interval)
                continue

            emb = np.asarray(face.normed_embedding, dtype=np.float32)
            if samples and float(samples[0] @ emb) < self._reg_consistency:
                # El "candidato" de este frame no es la misma persona que veníamos
                # acumulando: escena inestable, no arriesgamos un embedding mixto.
                info['ambiguos'] += 1
                time.sleep(self._reg_interval)
                continue
            samples.append(emb)
            if len(samples) >= self._reg_target:
                break
            time.sleep(self._reg_interval)

        if len(samples) < self._reg_min:
            return None, info
        mean = np.mean(np.vstack(samples), axis=0)
        return (mean / np.linalg.norm(mean)).astype(np.float32), info

    def _on_registrar(self, request, response):
        """Servicio de registro. Nunca lanza: siempre rellena response."""
        response.ok = False
        response.user_id = 0
        response.error = ''
        response.ya_registrado_como = ''
        response.similitud = 0.0

        nombre = (request.nombre or '').strip()
        response.nombre = nombre
        if not nombre:
            response.error = 'El nombre no puede estar vacío'
            return response

        self.get_logger().info(f'Registrando a "{nombre}"...')
        emb, info = self._capture_unknown()

        if emb is None:
            if info['frames'] == 0:
                response.error = ('No llegan frames de la cámara '
                                  '(¿está corriendo camera_node?)')
            elif info['con_caras'] == 0:
                response.error = 'No veo ninguna cara'
            elif info['ambiguos'] > 0 and info['max_desconocidos'] > 1:
                response.error = (
                    f'Veo {info["max_desconocidos"]} personas que no conozco y no '
                    'puedo distinguir quién quiere registrarse; pide que solo esa '
                    'persona se acerque y mire a la cámara, y reintenta'
                )
            elif info['max_desconocidos'] == 0 and info['conocido'] is not None:
                sim, known_name = info['conocido']
                response.ya_registrado_como = known_name
                response.similitud = round(sim, 4)
                response.error = (
                    f'No veo a nadie sin registrar: la persona en escena ya está '
                    f'registrada como "{known_name}" (similitud {sim:.2f})'
                )
            else:
                response.error = ('No conseguí una captura estable del rostro; '
                                  'pide que mire a la cámara y reintenta')
            self.get_logger().warn(f'Registro de "{nombre}" fallido: {response.error}')
            return response

        # Anti-duplicado final: el promedio podría matchear a alguien aunque los
        # frames individuales no lo hicieran.
        with self._db_lock:
            names, matrix = self._names, self._matrix
        if matrix.shape[0] > 0:
            sims = matrix @ emb
            best = int(np.argmax(sims))
            if float(sims[best]) >= self._threshold and names[best] != nombre:
                response.ya_registrado_como = names[best]
                response.similitud = round(float(sims[best]), 4)
                response.error = (
                    f'Esa cara ya está registrada como "{names[best]}" '
                    f'(similitud {sims[best]:.2f}); no registro un duplicado'
                )
                self.get_logger().warn(response.error)
                return response

        try:
            conn = self._connect()
            try:
                user_id = self._get_or_create_user(conn, nombre)
                emb_id = self._save_embedding(conn, user_id, emb)
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - el servicio nunca debe romperse
            response.error = f'Error de base de datos: {exc}'
            self.get_logger().error(response.error)
            return response

        self._append_to_catalog(user_id, nombre, emb)
        response.ok = True
        response.user_id = user_id
        self.get_logger().info(
            f'"{nombre}" (id={user_id}) registrado: embedding id={emb_id} '
            f'(media de {info["con_caras"]} frames con cara)'
        )
        return response

    # ------------------------------------------------------------------
    def destroy_node(self):
        self._stop.set()
        self._worker.join(timeout=5.0)
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = FaceRecognitionNode()
    # MultiThreadedExecutor: la suscripción sigue guardando frames frescos
    # mientras el callback del servicio de registro captura y procesa.
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
