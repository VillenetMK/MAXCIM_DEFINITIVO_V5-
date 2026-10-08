import math
import serial
import threading
import time
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist, TransformStamped
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from robot_base.telemetry import TelemetryBuffer

class RobotBaseNode(Node):

    def __init__(self):
        super().__init__('robot_base_node')

        # --- PARÁMETROS FÍSICOS Y DE CONFIGURACIÓN ---
        self.declare_parameter('base_frame', 'base_footprint')
        self.declare_parameter('port', '/dev/arduino')
        self.declare_parameter('baudrate', 115200)
        self.declare_parameter('wheel_separation', 0.65)  # Distancia entre centros de ruedas (m)
        self.declare_parameter('wheel_radius', 0.10)      # Radio de la rueda (m)
        self.declare_parameter('cmd_timeout', 0.5)         # Tiempo límite sin recibos para frenar (s)
        self.declare_parameter('max_wheel_speed', 0.65)   # Velocidad máxima por rueda (m/s)

        self.base_frame = self.get_parameter('base_frame').value
        self.telemetry = TelemetryBuffer()
        self.port = self.get_parameter('port').value
        self.baudrate = self.get_parameter('baudrate').value
        self.L = self.get_parameter('wheel_separation').value
        self.R = self.get_parameter('wheel_radius').value
        self.cmd_timeout = self.get_parameter('cmd_timeout').value
        self.max_wheel_speed = self.get_parameter('max_wheel_speed').value

        # --- ESTADOS DE ODOMETRÍA ---
        self.x = 0.0
        self.y = 0.0
        self.th = 0.0

        self.last_time = None
        self.last_cmd_time = self.get_clock().now()

        self.target_v = 0.0
        self.target_w = 0.0

        # Bloqueo de hilo para evitar colisiones en la comunicación Serie
        self.serial_lock = threading.Lock()

        # --- PUBLICADORES, SUSCRIPTORES Y TF ---
        self.odom_pub = self.create_publisher(Odometry, 'odom', 10)
        self.tf_broadcaster = TransformBroadcaster(self)

        self.cmd_sub = self.create_subscription(
            Twist,
            'cmd_vel',
            self.cmd_vel_callback,
            10
        )

        # --- CONEXIÓN CON ARDUINO ---
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.01)
            time.sleep(2.0)  # Esperar a que el Arduino se reinicie tras abrir el puerto
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
            self.get_logger().info(f'Conectado al Arduino en {self.port}')
        except serial.SerialException as e:
            self.get_logger().error(f'No se pudo abrir el puerto {self.port}: {e}')
            exit(1)

        # Timers: Lectura/Odometría a 50 Hz y Escritura/Watchdog a 20 Hz
        self.odom_timer = self.create_timer(0.02, self.update_odometry)
        self.cmd_timer = self.create_timer(0.05, self.control_loop)

    def cmd_vel_callback(self, msg: Twist):
        self.target_v = msg.linear.x
        self.target_w = msg.angular.z
        self.last_cmd_time = self.get_clock().now()

    def control_loop(self):
        """Cinemática Inversa: Transforma cmd_vel a velocidades para cada rueda"""
        now = self.get_clock().now()
        dt_cmd = (now - self.last_cmd_time).nanoseconds / 1e9

        # Watchdog: Frenar el robot si se pierden los comandos
        if dt_cmd > self.cmd_timeout:
            v_linear = 0.0
            v_angular = 0.0
        else:
            v_linear = self.target_v
            v_angular = self.target_w

        # Ecuaciones cinemáticas inversas
        v_der = v_linear + (v_angular * self.L / 2.0)
        v_izq = v_linear - (v_angular * self.L / 2.0)

        # Limitar al rango máximo seguro
        v_izq = max(min(v_izq, self.max_wheel_speed), -self.max_wheel_speed)
        v_der = max(min(v_der, self.max_wheel_speed), -self.max_wheel_speed)

        command_str = f"{v_izq:.3f},{v_der:.3f}\n"

        with self.serial_lock:
            try:
                self.ser.write(command_str.encode('utf-8'))
            except serial.SerialException as e:
                self.get_logger().error(f'Error al transmitir por puerto serie: {e}')

    def update_odometry(self):
        """Cinemática Directa: Transforma lecturas del Arduino a Pose (x, y, th)"""
        with self.serial_lock:
            if not self.ser.in_waiting:
                return
            try:
                sample = self.telemetry.feed(self.ser.read(self.ser.in_waiting))
            except serial.SerialException as e:
                self.get_logger().warn(f'Error de lectura serie: {e}')
                return

        if sample is None:
            return

        try:
            v_izq, v_der = sample
            current_time = self.get_clock().now()
            dt = ((current_time - self.last_time).nanoseconds / 1e9
                  if self.last_time is not None else 0.0)
            self.last_time = current_time

            if dt < 0.0:
                return

            # Ecuaciones cinemáticas directas
            v = (v_der + v_izq) / 2.0
            w = (v_der - v_izq) / self.L

            self.x += (v * math.cos(self.th)) * dt
            self.y += (v * math.sin(self.th)) * dt
            self.th += w * dt

            qz = math.sin(self.th / 2.0)
            qw = math.cos(self.th / 2.0)

            # Publicar TF (odom -> base_footprint -> base_link -> laser)
            t = TransformStamped()
            t.header.stamp = current_time.to_msg()
            t.header.frame_id = 'odom'
            t.child_frame_id = self.base_frame
            t.transform.translation.x = self.x
            t.transform.translation.y = self.y
            t.transform.rotation.z = qz
            t.transform.rotation.w = qw
            self.tf_broadcaster.sendTransform(t)

            # Publicar mensaje Odometry en /odom
            odom = Odometry()
            odom.header.stamp = current_time.to_msg()
            odom.header.frame_id = 'odom'
            odom.child_frame_id = self.base_frame
            odom.pose.pose.position.x = self.x
            odom.pose.pose.position.y = self.y
            odom.pose.pose.orientation.z = qz
            odom.pose.pose.orientation.w = qw
            odom.twist.twist.linear.x = v
            odom.twist.twist.angular.z = w

            # Covarianzas estructuradas para compatibilidad con Nav2
            odom.pose.covariance = [
                0.001, 0.0,   0.0,  0.0,  0.0,  0.0,
                0.0,   0.001, 0.0,  0.0,  0.0,  0.0,
                0.0,   0.0,   1e6,  0.0,  0.0,  0.0,
                0.0,   0.0,   0.0,  1e6,  0.0,  0.0,
                0.0,   0.0,   0.0,  0.0,  1e6,  0.0,
                0.0,   0.0,   0.0,  0.0,  0.0,  0.03
            ]

            odom.twist.covariance = [
                0.001, 0.0,   0.0,  0.0,  0.0,  0.0,
                0.0,   0.001, 0.0,  0.0,  0.0,  0.0,
                0.0,   0.0,   1e6,  0.0,  0.0,  0.0,
                0.0,   0.0,   0.0,  1e6,  0.0,  0.0,
                0.0,   0.0,   0.0,  0.0,  1e6,  0.0,
                0.0,   0.0,   0.0,  0.0,  0.0,  0.03
            ]

            self.odom_pub.publish(odom)

        except Exception as e:
            self.get_logger().warn(f'Error al procesar trama de Arduino: {e}')

def main(args=None):
    rclpy.init(args=args)
    node = RobotBaseNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.ser.write(b"0.00,0.00\n")
            node.ser.close()
        except Exception:
            pass
        node.destroy_node()
        rclpy.shutdown()

if __name__ == '__main__':
    main()
