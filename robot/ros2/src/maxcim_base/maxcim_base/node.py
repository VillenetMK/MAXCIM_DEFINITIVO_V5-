"""Puente USB físico. No abre puertos automáticamente ni produce pulsos ficticios."""

import secrets
import json
import time
from math import isfinite

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
import serial
from geometry_msgs.msg import Twist
from std_msgs.msg import String
from std_srvs.srv import SetBool, Trigger
from maxcim_interfaces.msg import WheelFeedback

from .kinematics import Geometry, wheel_targets
from .protocol import encode, decode, parse_identity, parse_telemetry, LineBuffer
from .safety import DriveGate, FeedbackClock


class NanoBase(Node):
    def __init__(self):
        super().__init__('nano_base')
        defaults = {
            'serial_port': '', 'calibration_confirmed': False,
            'forward_direction_confirmed': False,
            'left_wheel_radius_m': 0.0, 'right_wheel_radius_m': 0.0,
            'wheel_separation_m': 0.0, 'left_pulses_per_revolution': 0.0,
            'right_pulses_per_revolution': 0.0, 'max_wheel_speed_mps': 0.10,
            'feedback_timeout_s': 0.25, 'command_timeout_s': 0.30,
            'max_wheel_acceleration_mps2': 0.10, 'base_frame': 'base_link',
        }
        for key, value in defaults.items():
            self.declare_parameter(key, value)
        p = lambda name: self.get_parameter(name).value
        if self.get_parameter('use_sim_time').value:
            raise ValueError('Este puente físico requiere use_sim_time=false.')
        if not p('serial_port'):
            raise ValueError('Falta serial_port; usa /dev/serial/by-id/... del Nano.')
        self.geometry = None
        if p('calibration_confirmed'):
            self.geometry = Geometry(
                p('left_wheel_radius_m'), p('right_wheel_radius_m'),
                p('wheel_separation_m'), p('left_pulses_per_revolution'),
                p('right_pulses_per_revolution'), p('max_wheel_speed_mps'))
        self.forward_confirmed = p('forward_direction_confirmed')
        self.acceleration = p('max_wheel_acceleration_mps2')
        if not isfinite(self.acceleration) or self.acceleration <= 0:
            raise ValueError('La aceleración debe ser positiva y finita.')
        self.gate = DriveGate(p('feedback_timeout_s'), p('command_timeout_s'))
        self.clock_tracker = FeedbackClock(p('feedback_timeout_s'))
        self.base_frame = p('base_frame')
        self.session = secrets.randbelow(0xffffffff) + 1
        self.command_seq = 0
        self.identified = False
        self.lines = LineBuffer()
        self.last_hello = -10.0
        self.last_write = 0.0
        self.last_status = 0.0
        self.applied = (0.0, 0.0)
        self.ack_at = time.monotonic()
        self.last_ack = 0
        self.publisher = self.create_publisher(WheelFeedback, '/wheel_feedback', 10)
        self.status_pub = self.create_publisher(String, '~/status', 1)
        # KEEP_LAST(1), VOLATILE: no reproducir una cola de órdenes antiguas.
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.create_subscription(Twist, '/cmd_vel', self.on_command, qos)
        self.create_service(SetBool, '~/enable', self.on_enable)
        self.create_service(Trigger, '~/stop', self.on_stop)
        self.port = serial.Serial(p('serial_port'), 115200, timeout=0,
                                  write_timeout=0.03, exclusive=True)
        self.port.reset_input_buffer()
        self.timer = self.create_timer(0.01, self.poll,
                                      clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.get_logger().info('Nano abierto; verificando identidad. Motores deshabilitados.')

    def write(self, payload):
        packet = encode(payload)
        if self.port.write(packet) != len(packet):
            raise serial.SerialException('Escritura serie incompleta')

    def stop(self, reason):
        self.gate.stop(reason)
        self.applied = (0.0, 0.0)
        if self.identified and self.port.is_open:
            self.command_seq += 1
            self.write(f'S {self.session} {self.command_seq}')

    def on_enable(self, request, response):
        if not request.data:
            self.stop('Deshabilitado por operador')
            response.success = True
        else:
            response.success = self.identified and self.gate.enable(
                time.monotonic(), self.geometry is not None and self.forward_confirmed)
        response.message = self.gate.reason
        return response

    def on_stop(self, request, response):
        self.stop('Parada solicitada')
        response.success, response.message = True, self.gate.reason
        return response

    def on_command(self, msg):
        if not self.gate.enabled or self.geometry is None:
            return
        values = (msg.linear.x, msg.linear.y, msg.linear.z,
                  msg.angular.x, msg.angular.y, msg.angular.z)
        try:
            if not all(isfinite(v) for v in values):
                raise ValueError('cmd_vel no finito')
            if any(abs(v) > 1e-9 for v in (msg.linear.y, msg.linear.z,
                                          msg.angular.x, msg.angular.y)):
                raise ValueError('La base solo admite linear.x y angular.z')
            target = wheel_targets(msg.linear.x, msg.angular.z, self.geometry)
            self.gate.command(time.monotonic(), target)
        except ValueError as exc:
            self.stop(str(exc))
            self.get_logger().error(str(exc))

    def receive(self, line, now):
        payload = decode(line)
        if payload.startswith('I '):
            identity = parse_identity(payload)
            if identity.session != self.session:
                return
            if not self.identified:
                self.identified = True
                self.ack_at = now
                self.get_logger().info('Identidad MAXCIM_BASE v1 confirmada.')
            return
        if not self.identified:
            return
        t = parse_telemetry(payload)
        if t.session != self.session:
            raise ValueError('Cambió la sesión del Nano')
        age = self.clock_tracker.accept(t.sequence, t.uptime_ms, now)
        if t.command_ack > self.command_seq or t.command_ack < self.last_ack:
            raise ValueError('Confirmación de mando fuera de secuencia')
        if t.command_ack > self.last_ack:
            self.last_ack, self.ack_at = t.command_ack, now
        self.gate.feedback(now - age, t.status)
        msg = WheelFeedback()
        stamp_ns = self.get_clock().now().nanoseconds - int(age * 1e9)
        msg.header.stamp.sec = stamp_ns // 1000000000
        msg.header.stamp.nanosec = stamp_ns % 1000000000
        msg.header.frame_id = self.base_frame
        for field in ('session', 'sequence', 'uptime_ms', 'left_ticks', 'right_ticks',
                      'left_pps', 'right_pps', 'left_pwm_permille', 'right_pwm_permille',
                      'status', 'command_ack'):
            setattr(msg, field, getattr(t, field))
        msg.direction_valid = bool(self.forward_confirmed and not (t.status & 5))
        self.publisher.publish(msg)

    def reset_link(self, reason):
        self.stop(reason)
        self.identified = False
        self.session = secrets.randbelow(0xffffffff) + 1
        self.command_seq = self.last_ack = 0
        self.clock_tracker = FeedbackClock(self.gate.feedback_timeout)
        self.gate.feedback_at = None
        self.lines = LineBuffer()
        self.port.reset_input_buffer()
        self.last_hello = -10.0

    def poll(self):
        now = time.monotonic()
        try:
            count = self.port.in_waiting
            if count > 4096:
                self.reset_link('Cola serie excesiva')
                return
            for line in self.lines.feed(self.port.read(count)):
                try:
                    self.receive(line, now)
                except ValueError as exc:
                    self.reset_link(str(exc))
                    break
            if not self.identified:
                if now - self.last_hello >= 1.0:
                    self.write(f'H {self.session}')
                    self.last_hello = now
            elif now - self.last_write >= 0.05:
                if (self.gate.feedback_at is not None and
                        now - self.gate.feedback_at >= self.gate.feedback_timeout):
                    self.reset_link('Nano sin telemetría reciente')
                    return
                if self.command_seq and now - self.ack_at > 0.30:
                    self.reset_link('Nano no confirma los mandos')
                    return
                output = self.gate.output(now)
                dt = min(0.10, now - self.last_write)
                self.last_write = now
                self.command_seq += 1
                if self.command_seq >= 0xfffffffe:
                    self.reset_link('Renovación de secuencia; requiere habilitar')
                    return
                if output is None or output == (0.0, 0.0):
                    self.applied = (0.0, 0.0)
                    self.write(f'S {self.session} {self.command_seq}')
                else:
                    # Limitar aceleración en metros/s² respetando el ratio del cambio.
                    from math import pi
                    g = self.geometry
                    scales = (2*pi*g.left_radius_m/g.left_ticks_per_revolution,
                              2*pi*g.right_radius_m/g.right_ticks_per_revolution)
                    largest = max(abs((v - old) * scale)
                                  for v, old, scale in zip(output, self.applied, scales))
                    fraction = min(1.0, self.acceleration * dt / largest) if largest else 1.0
                    self.applied = tuple(old + (v-old)*fraction
                                         for v, old in zip(output, self.applied))
                    self.write(f'V {self.session} {self.command_seq} '
                               f'{self.applied[0]:.3f} {self.applied[1]:.3f}')
            if now - self.last_status >= 0.5:
                status = String()
                status.data = json.dumps({"enabled": self.gate.enabled,
                    "identified": self.identified, "reason": self.gate.reason})
                self.status_pub.publish(status)
                self.last_status = now
        except (OSError, serial.SerialException) as exc:
            self.gate.stop('Puerto desconectado: ' + str(exc))
            self.get_logger().error(self.gate.reason + '; el watchdog del Nano detiene.')
            self.timer.cancel()
            self.port.close()

    def destroy_node(self):
        if hasattr(self, 'port'):
            try:
                self.stop('Cierre del nodo')
            except (OSError, serial.SerialException):
                pass
            self.port.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = NanoBase()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
