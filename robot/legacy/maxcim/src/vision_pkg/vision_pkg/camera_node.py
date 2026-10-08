"""Nodo de cámara.

Captura frames de una webcam/USB y los publica para el resto del sistema.

Por defecto publica **JPEG comprimido** (``sensor_msgs/CompressedImage`` en
``<image_topic>/compressed``): una imagen 640x480 baja de ~900 KB (raw bgr8) a
~30 KB, aliviando ~30x el bus DDS y evitando que el tráfico de vídeo
desestabilice a los demás nodos. Con ``use_compression:=false`` vuelve a
publicar ``sensor_msgs/Image`` raw en ``<image_topic>``.

El QoS es de tipo sensor (BEST_EFFORT, KEEP_LAST, depth=1): nunca bloquea a un
suscriptor lento y siempre entrega el frame más reciente sin acumular backlog.

Parámetros ROS2
---------------
camera_index     (int,   0)
publish_rate     (float, 15.0)              : Hz de publicación
frame_id         (str,   'camera_frame')
width            (int,   0)                 : 0 = no forzar
height           (int,   0)                 : 0 = no forzar
image_topic      (str,   'camera/image_raw'): base del topic
use_compression  (bool,  True)             : publica JPEG comprimido
jpeg_quality     (int,   80)               : calidad JPEG (1-100)
"""

import threading

import cv2
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image


class CameraNode(Node):

    def __init__(self):
        super().__init__('camera_node')

        self.declare_parameter('camera_index', 0)
        self.declare_parameter('publish_rate', 15.0)
        self.declare_parameter('frame_id', 'camera_frame')
        self.declare_parameter('width', 0)   # 0 = no forzar
        self.declare_parameter('height', 0)  # 0 = no forzar
        self.declare_parameter('image_topic', 'camera/image_raw')
        self.declare_parameter('use_compression', True)
        self.declare_parameter('jpeg_quality', 80)

        camera_index = self.get_parameter('camera_index').value
        publish_rate  = float(self.get_parameter('publish_rate').value)
        self.frame_id = self.get_parameter('frame_id').value
        width  = int(self.get_parameter('width').value)
        height = int(self.get_parameter('height').value)
        image_topic = self.get_parameter('image_topic').value
        self._use_compression = bool(self.get_parameter('use_compression').value)
        self._jpeg_quality = int(self.get_parameter('jpeg_quality').value)

        self.bridge = CvBridge()

        # QoS de sensor: best-effort + sólo el frame más reciente. No bloquea a
        # los suscriptores ni acumula backlog si alguno va lento.
        qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        if self._use_compression:
            self.publisher_ = self.create_publisher(
                CompressedImage, f'{image_topic}/compressed', qos
            )
        else:
            self.publisher_ = self.create_publisher(Image, image_topic, qos)

        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            self.get_logger().error(f'No se pudo abrir la cámara {camera_index}')
            raise RuntimeError('Camera not available')

        self.cap.set(cv2.CAP_PROP_FPS, publish_rate)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # descarta frames acumulados
        if width  > 0: self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        if height > 0: self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        # Hilo de captura: bloquea en cap.read() sin ocupar el executor de ROS
        self._lock   = threading.Lock()
        self._frame  = None
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()

        self.create_timer(1.0 / publish_rate, self._publish)
        modo = f'JPEG q={self._jpeg_quality}' if self._use_compression else 'raw bgr8'
        self.get_logger().info(
            f'CameraNode listo — índice={camera_index} @ {publish_rate} Hz ({modo})'
        )

    def _capture_loop(self):
        """Captura el frame más reciente; cap.read() bloquea en el driver, no gira en vacío."""
        while not self._stop.is_set():
            ret, frame = self.cap.read()
            if not ret:
                continue
            with self._lock:
                self._frame = frame

    def _publish(self):
        with self._lock:
            frame = self._frame
        if frame is None:
            return

        stamp = self.get_clock().now().to_msg()
        if self._use_compression:
            ok, buf = cv2.imencode(
                '.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), self._jpeg_quality]
            )
            if not ok:
                self.get_logger().warn('Fallo al codificar JPEG; se omite frame')
                return
            msg = CompressedImage()
            msg.format = 'jpeg'
            msg.data = buf.tobytes()
        else:
            msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')

        msg.header.stamp = stamp
        msg.header.frame_id = self.frame_id
        self.publisher_.publish(msg)

    def destroy_node(self):
        self._stop.set()
        self._thread.join(timeout=2.0)
        if self.cap.isOpened():
            self.cap.release()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
