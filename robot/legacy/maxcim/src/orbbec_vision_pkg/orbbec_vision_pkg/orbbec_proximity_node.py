"""Nodo de proximidad para Orbbec Gemini 2.

Analiza la zona central del depth map y publica:
- orbbec/proximity   (std_msgs/Float32) — distancia mínima en metros al objeto
                      más cercano en la zona central. 0.0 si no hay datos válidos.
- orbbec/presence    (std_msgs/Bool)    — True si alguien está dentro de
                      `presence_threshold` metros.

Parámetros ROS2
---------------
depth_topic           (str,   'camera/depth/image_raw') : topic de profundidad
publish_rate          (float, 5.0)   : Hz de publicación
zone_fraction         (float, 0.3)   : fracción del frame analizada (30% central)
presence_threshold    (float, 2.0)   : metros; por debajo → presencia = True
min_valid_depth_mm    (int,   150)   : umbral mínimo para ignorar ruido de superficie
max_valid_depth_mm    (int,   6000)  : umbral máximo (6 m, rango útil Gemini 2)
noise_percentile      (float, 5.0)   : percentil del histograma para ignorar píxeles
                                       aislados (5 = 5º percentil de valores válidos)
"""

import threading

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSHistoryPolicy, QoSProfile, QoSReliabilityPolicy
from sensor_msgs.msg import Image
from std_msgs.msg import Bool, Float32


class OrbbecProximityNode(Node):

    def __init__(self):
        super().__init__('orbbec_proximity_node')

        self.declare_parameter('depth_topic',        'camera/depth/image_raw')
        self.declare_parameter('publish_rate',        5.0)
        self.declare_parameter('zone_fraction',       0.3)
        self.declare_parameter('presence_threshold',  2.0)
        self.declare_parameter('min_valid_depth_mm',  150)
        self.declare_parameter('max_valid_depth_mm',  6000)
        self.declare_parameter('noise_percentile',    5.0)

        depth_topic   = self.get_parameter('depth_topic').value
        publish_rate  = float(self.get_parameter('publish_rate').value)
        self._zone    = float(self.get_parameter('zone_fraction').value)
        self._thresh  = float(self.get_parameter('presence_threshold').value)
        self._min_mm  = int(self.get_parameter('min_valid_depth_mm').value)
        self._max_mm  = int(self.get_parameter('max_valid_depth_mm').value)
        self._pct     = float(self.get_parameter('noise_percentile').value)

        sensor_qos = QoSProfile(
            reliability=QoSReliabilityPolicy.BEST_EFFORT,
            history=QoSHistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self._depth_sub = self.create_subscription(
            Image, depth_topic, self._on_depth, sensor_qos
        )

        self._prox_pub    = self.create_publisher(Float32, 'orbbec/proximity', 10)
        self._presence_pub = self.create_publisher(Bool,    'orbbec/presence',  10)

        self._lock        = threading.Lock()
        self._latest_depth: np.ndarray | None = None

        self.create_timer(1.0 / publish_rate, self._publish)

        self.get_logger().info(
            f'OrbbecProximityNode listo | depth="{depth_topic}" | {publish_rate} Hz | '
            f'zona={int(self._zone*100)}% | umbral presencia={self._thresh} m'
        )

    def _on_depth(self, msg: Image):
        try:
            depth = np.frombuffer(bytes(msg.data), dtype=np.uint16).reshape(
                (msg.height, msg.width)
            )
        except Exception:
            return
        with self._lock:
            self._latest_depth = depth.copy()

    def _publish(self):
        with self._lock:
            depth = self._latest_depth

        dist_m = 0.0
        if depth is not None:
            h, w = depth.shape
            # Recortar la zona central del frame
            margin_y = int(h * (1.0 - self._zone) / 2)
            margin_x = int(w * (1.0 - self._zone) / 2)
            roi = depth[margin_y: h - margin_y, margin_x: w - margin_x]

            valid = roi[(roi >= self._min_mm) & (roi <= self._max_mm)]
            if valid.size > 0:
                # Percentil bajo para ignorar píxeles aislados de ruido
                dist_m = float(np.percentile(valid, self._pct)) / 1000.0

        prox_msg = Float32()
        prox_msg.data = round(dist_m, 3)
        self._prox_pub.publish(prox_msg)

        pres_msg = Bool()
        pres_msg.data = (0.0 < dist_m <= self._thresh)
        self._presence_pub.publish(pres_msg)


def main(args=None):
    rclpy.init(args=args)
    node = OrbbecProximityNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
