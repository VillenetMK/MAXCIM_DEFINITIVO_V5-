from sensor_msgs.msg import LaserScan
import rclpy
from rclpy.node import Node


class ScanRelayNode(Node):

  def __init__(self):
    super().__init__('scan_relay_node')

    # Se suscribe al escaneo original del RPLiDAR
    self.subscription = self.create_subscription(
        LaserScan, '/scan_raw', self.scan_callback, 10
    )

    # Publica el escaneo corregido con el tiempo exacto actual
    self.publisher = self.create_publisher(LaserScan, '/scan', 10)
    self.get_logger().info('Scan Relay Node iniciado: re-estampando /scan')

  def scan_callback(self, msg: LaserScan):
    # Re-estampar con la hora exacta actual del sistema ROS 2
    msg.header.stamp = self.get_clock().now().to_msg()
    self.publisher.publish(msg)


def main(args=None):
  rclpy.init(args=args)
  node = ScanRelayNode()
  try:
    rclpy.spin(node)
  except KeyboardInterrupt:
    pass
  finally:
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
  main()
