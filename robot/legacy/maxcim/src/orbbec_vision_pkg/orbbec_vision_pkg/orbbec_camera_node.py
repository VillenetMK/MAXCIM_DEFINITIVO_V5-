"""Nodo de cámara para Orbbec Gemini 2.

Publica:
- camera/image_raw/compressed  (sensor_msgs/CompressedImage, JPEG)  — color alineado
- camera/depth/image_raw        (sensor_msgs/Image, 16UC1, mm)       — depth alineado al color
- camera/depth_overlay/compressed (sensor_msgs/CompressedImage, JPEG) — color + depth coloreado

Usa pyorbbecsdk + AlignFilter (software D2C) para que cada píxel del mapa de
profundidad corresponda al mismo punto del color frame. Esto permite a
orbbec_face_recognition_node leer la profundidad real en las coordenadas del
bounding box de la cara.

Sustituye a vision_pkg/camera_node (que usaba cv2.VideoCapture). Publica en los
mismos topics de color para que el resto del sistema (gemini_live_node,
face_recognition_node) no requiera ningún cambio.

Parámetros ROS2
---------------
publish_rate        (float, 15.0) : Hz de publicación
width               (int,   0)    : resolución color; 0 = auto
height              (int,   0)    : resolución color; 0 = auto
frame_id            (str,   'camera_frame')
image_topic         (str,   'camera/image_raw')       : topic base color
depth_topic         (str,   'camera/depth/image_raw') : topic profundidad
jpeg_quality        (int,   80)   : calidad JPEG (1-100)
depth_overlay_alpha (float, 0.3)  : transparencia del mapa de profundidad en el overlay
"""

import ctypes
import threading

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    QoSDurabilityPolicy,
    QoSHistoryPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
)
from sensor_msgs.msg import CameraInfo, CompressedImage, Image
from std_msgs.msg import Header

try:
    from pyorbbecsdk import (  # type: ignore
        AlignFilter,
        Config,
        OBFormat,
        OBFrameAggregateOutputMode,
        OBSensorType,
        OBStreamType,
        Pipeline,
    )
except ImportError as e:
    raise SystemExit(
        'Falta pyorbbecsdk: cd /home/maxcim/pyorbbecsdk && pip install -e .'
    ) from e


class OrbbecCameraNode(Node):

    def __init__(self):
        super().__init__('orbbec_camera_node')

        self.declare_parameter('publish_rate', 15.0)
        self.declare_parameter('width', 0)
        self.declare_parameter('height', 0)
        self.declare_parameter('frame_id', 'camera_frame')
        self.declare_parameter('image_topic', 'camera/image_raw')
        self.declare_parameter('depth_topic', 'camera/depth/image_raw')
        self.declare_parameter('jpeg_quality', 80)
        self.declare_parameter('depth_overlay_alpha', 0.3)

        publish_rate          = float(self.get_parameter('publish_rate').value)
        self._req_width       = int(self.get_parameter('width').value)
        self._req_height      = int(self.get_parameter('height').value)
        self._frame_id        = self.get_parameter('frame_id').value
        image_topic           = self.get_parameter('image_topic').value
        depth_topic           = self.get_parameter('depth_topic').value
        self._jpeg_quality    = int(self.get_parameter('jpeg_quality').value)
        self._overlay_alpha   = float(self.get_parameter('depth_overlay_alpha').value)

        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._color_pub = self.create_publisher(
            CompressedImage, f'{image_topic}/compressed', qos
        )
        self._depth_pub = self.create_publisher(Image, depth_topic, qos)
        self._overlay_pub = self.create_publisher(
            CompressedImage, 'camera/depth_overlay/compressed', qos
        )

        # CameraInfo — transient_local (latched) para que suscriptores tardíos lo reciban
        latched_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.RELIABLE,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._info_pub = self.create_publisher(
            CameraInfo, f'{image_topic}/camera_info', latched_qos
        )

        self._pipeline, self._align_filter = self._init_pipeline()
        self._camera_info_msg = self._build_camera_info(image_topic)

        self._lock          = threading.Lock()
        self._latest_color: np.ndarray | None = None  # BGR uint8
        self._latest_depth: np.ndarray | None = None  # uint16 mm alineado al color
        self._stop          = threading.Event()
        self._thread        = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        self.create_timer(1.0 / publish_rate, self._publish)
        self.get_logger().info(
            f'OrbbecCameraNode listo — color={image_topic}/compressed  '
            f'depth={depth_topic}  overlay=camera/depth_overlay/compressed  @ {publish_rate} Hz'
        )

    # ------------------------------------------------------------------
    # Inicialización del pipeline
    # ------------------------------------------------------------------

    def _init_pipeline(self):
        pipeline = Pipeline()

        try:
            pipeline.enable_frame_sync()
        except Exception as exc:
            self.get_logger().warn(f'Frame sync no disponible: {exc}')

        config = Config()

        # Color — intentar perfil RGB explícito (requerido por AlignFilter D2C)
        try:
            profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
            color_profile = profile_list.get_video_stream_profile(
                self._req_width, self._req_height, OBFormat.RGB, 0
            )
            config.enable_stream(color_profile)
            self.get_logger().info(
                f'Perfil color: {color_profile.get_width()}×{color_profile.get_height()} '
                f'RGB @ {color_profile.get_fps()} fps'
            )
        except Exception as exc:
            self.get_logger().warn(f'No se pudo fijar perfil RGB ({exc}); usando default')
            profile_list = pipeline.get_stream_profile_list(OBSensorType.COLOR_SENSOR)
            config.enable_stream(profile_list.get_default_video_stream_profile())

        # Depth — perfil por defecto
        try:
            depth_list = pipeline.get_stream_profile_list(OBSensorType.DEPTH_SENSOR)
            depth_profile = depth_list.get_default_video_stream_profile()
            config.enable_stream(depth_profile)
            self.get_logger().info(
                f'Perfil depth: {depth_profile.get_width()}×{depth_profile.get_height()} '
                f'@ {depth_profile.get_fps()} fps'
            )
        except Exception as exc:
            self.get_logger().error(f'No se pudo configurar el stream de profundidad: {exc}')
            raise

        # Requerir ambos frames antes de entregar FrameSet
        try:
            config.set_frame_aggregate_output_mode(OBFrameAggregateOutputMode.FULL_FRAME_REQUIRE)
        except Exception:
            pass

        pipeline.start(config)

        align_filter = AlignFilter(align_to_stream=OBStreamType.COLOR_STREAM)
        self.get_logger().info('Pipeline Orbbec iniciado — AlignFilter D2C (software)')
        return pipeline, align_filter

    # ------------------------------------------------------------------
    # Intrínsecos de la cámara
    # ------------------------------------------------------------------

    def _build_camera_info(self, image_topic: str) -> CameraInfo:
        try:
            param = self._pipeline.get_camera_param()
            ri = param.rgb_intrinsic
            rd = param.rgb_distortion
            fx, fy, cx, cy = ri.fx, ri.fy, ri.cx, ri.cy
            w, h = ri.width, ri.height
            d = [float(rd.k1), float(rd.k2), float(rd.p1), float(rd.p2), float(rd.k3)]
        except Exception as exc:
            self.get_logger().warn(f'Intrínsecos no disponibles ({exc}); usando defaults 640×480')
            fx, fy, cx, cy = 517.414, 517.424, 321.551, 238.426
            w, h = self._req_width or 640, self._req_height or 480
            d = [0.0, 0.0, 0.0, 0.0, 0.0]

        msg = CameraInfo()
        msg.header.frame_id = self._frame_id
        msg.width  = w
        msg.height = h
        msg.distortion_model = 'plumb_bob'
        msg.d = d
        msg.k = [fx,  0.0, cx,
                 0.0, fy,  cy,
                 0.0, 0.0, 1.0]
        msg.r = [1.0, 0.0, 0.0,
                 0.0, 1.0, 0.0,
                 0.0, 0.0, 1.0]
        msg.p = [fx,  0.0, cx,  0.0,
                 0.0, fy,  cy,  0.0,
                 0.0, 0.0, 1.0, 0.0]
        self.get_logger().info(
            f'Intrínsecos color: fx={fx:.3f} fy={fy:.3f} cx={cx:.3f} cy={cy:.3f} '
            f'→ {image_topic}/camera_info'
        )
        return msg

    # ------------------------------------------------------------------
    # Hilo de captura
    # ------------------------------------------------------------------

    def _capture_loop(self):
        while not self._stop.is_set():
            try:
                frames = self._pipeline.wait_for_frames(1000)
                if not frames:
                    continue

                # Color: tomamos del frameset ORIGINAL antes del AlignFilter.
                # El AlignFilter procesa internamente el buffer de color y puede
                # dejar el frame con datos corruptos (todo 128) dependiendo del
                # dispositivo. Solo necesitamos que el depth quede alineado.
                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue

                # Depth: del frameset ALINEADO (proyectado al espacio del color).
                aligned = self._align_filter.process(frames)
                depth_frame = aligned.get_depth_frame() if aligned else None

                color_img = self._to_bgr(color_frame)
                if color_img is None:
                    continue

                depth_img = self._to_depth_mm(depth_frame) if depth_frame else None

                with self._lock:
                    self._latest_color = color_img
                    self._latest_depth = depth_img

            except Exception as exc:
                self.get_logger().warn(f'Error en captura Orbbec: {exc}')

    # ------------------------------------------------------------------
    # Conversión de frames
    # ------------------------------------------------------------------

    @staticmethod
    def _frame_to_raw(frame, n_bytes: int) -> np.ndarray:
        """Lee n_bytes del buffer real del SDK, ignorando el stride=0 del ndarray."""
        data = frame.get_data()  # numpy array con strides no estándar (stride=0)
        raw = ctypes.string_at(data.ctypes.data, n_bytes)
        return np.frombuffer(raw, dtype=np.uint8)

    def _to_bgr(self, color_frame) -> np.ndarray | None:
        try:
            h, w = color_frame.get_height(), color_frame.get_width()
            fmt  = color_frame.get_format()
            if fmt == OBFormat.MJPG:
                # MJPG: tamaño variable — leer toda la data de la API
                raw = self._frame_to_raw(color_frame, len(color_frame.get_data()))
                return cv2.imdecode(raw, cv2.IMREAD_COLOR)
            if fmt in (OBFormat.RGB, OBFormat.BGR):
                raw = self._frame_to_raw(color_frame, h * w * 3)
                img = raw.reshape((h, w, 3))
                return cv2.cvtColor(img, cv2.COLOR_RGB2BGR if fmt == OBFormat.RGB else cv2.COLOR_BGR2RGB)
            if fmt == OBFormat.YUYV:
                raw = self._frame_to_raw(color_frame, h * w * 2)
                return cv2.cvtColor(raw.reshape((h, w, 2)), cv2.COLOR_YUV2BGR_YUYV)
            # Fallback
            raw = self._frame_to_raw(color_frame, h * w * 3)
            return cv2.cvtColor(raw.reshape((h, w, 3)), cv2.COLOR_RGB2BGR)
        except Exception as exc:
            self.get_logger().debug(f'_to_bgr fallo: {exc}')
            return None

    def _make_overlay(self, color_bgr: np.ndarray, depth_mm: np.ndarray) -> np.ndarray:
        """Mezcla color + mapa de profundidad coloreado (TURBO, alpha configurable)."""
        depth_norm = np.clip(depth_mm.astype(np.float32) / 6000.0 * 255.0, 0, 255).astype(np.uint8)
        colored = cv2.applyColorMap(depth_norm, cv2.COLORMAP_TURBO)
        colored[depth_mm == 0] = 0  # sin dato → negro, no color falso
        if colored.shape[:2] != color_bgr.shape[:2]:
            colored = cv2.resize(colored, (color_bgr.shape[1], color_bgr.shape[0]))
        return cv2.addWeighted(color_bgr, 1.0 - self._overlay_alpha, colored, self._overlay_alpha, 0)

    def _to_depth_mm(self, depth_frame) -> np.ndarray | None:
        try:
            h, w  = depth_frame.get_height(), depth_frame.get_width()
            raw   = self._frame_to_raw(depth_frame, h * w * 2)
            data  = raw.view(np.uint16).reshape((h, w))
            scale = depth_frame.get_depth_scale()
            # Gemini 2: scale suele ser 1.0 (ya en mm) o 0.1 (en 0.1mm → ×10 = mm).
            if abs(scale - 1.0) > 1e-4 and scale > 0:
                data = np.clip(
                    (data.astype(np.float32) * scale), 0, 65535
                ).astype(np.uint16)
            return data
        except Exception as exc:
            self.get_logger().debug(f'_to_depth_mm fallo: {exc}')
            return None

    # ------------------------------------------------------------------
    # Timer de publicación
    # ------------------------------------------------------------------

    def _publish(self):
        with self._lock:
            color = self._latest_color
            depth = self._latest_depth

        # Color y depth se publican de forma independiente: si depth aún no está
        # disponible, el color sigue llegando a gemini_live_node y face_recognition.
        if color is None:
            return

        stamp = self.get_clock().now().to_msg()

        # Color → JPEG CompressedImage
        ok, buf = cv2.imencode(
            '.jpg', color, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
        )
        if ok:
            cmsg = CompressedImage()
            cmsg.header.stamp    = stamp
            cmsg.header.frame_id = self._frame_id
            cmsg.format          = 'jpeg'
            cmsg.data            = buf.tobytes()
            self._color_pub.publish(cmsg)

        # Depth → sensor_msgs/Image 16UC1 (mm)
        # Construimos el mensaje manualmente para evitar depender de cv_bridge,
        # que falla silenciosamente con NumPy 2.x en Jazzy (compilado contra NumPy 1.x).
        if depth is not None:
            dmsg = Image()
            dmsg.header.stamp    = stamp
            dmsg.header.frame_id = self._frame_id
            dmsg.height          = depth.shape[0]
            dmsg.width           = depth.shape[1]
            dmsg.encoding        = '16UC1'
            dmsg.is_bigendian    = False
            dmsg.step            = depth.shape[1] * 2  # 2 bytes por píxel uint16
            dmsg.data            = depth.tobytes()
            self._depth_pub.publish(dmsg)

        # Depth overlay → JPEG con color + profundidad coloreada superpuesta
        if depth is not None:
            overlay = self._make_overlay(color, depth)
            ok2, buf2 = cv2.imencode(
                '.jpg', overlay, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if ok2:
                omsg = CompressedImage()
                omsg.header.stamp    = stamp
                omsg.header.frame_id = self._frame_id
                omsg.format          = 'jpeg'
                omsg.data            = buf2.tobytes()
                self._overlay_pub.publish(omsg)

        # CameraInfo — mismo timestamp que el frame de color
        self._camera_info_msg.header.stamp = stamp
        self._info_pub.publish(self._camera_info_msg)

    # ------------------------------------------------------------------

    def destroy_node(self):
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self._pipeline.stop()
        except Exception:
            pass
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = OrbbecCameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
