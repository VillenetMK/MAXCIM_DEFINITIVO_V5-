"""Única entrada a actuadores: concesión, límites y watchdog monotónico."""
import json
import math
import time
import uuid

import rclpy
from rclpy.node import Node
from rclpy.clock import Clock, ClockType
from rclpy.qos import QoSProfile, DurabilityPolicy, qos_profile_sensor_data
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan
from std_msgs.msg import String
from maxcim_interfaces.msg import RobotState, WheelFeedback
from maxcim_interfaces.srv import ExecuteCommand
from maxcim_core.control import Controller, Limits
from maxcim_core.simulation import SimulatedActuators
from maxcim_api.bridge import dispatch

ARMS = {"home", "saludar", "levantar_manos", "dar_mano", "dar_mano_izquierda",
        "bailar", "brazo_derecho_arriba", "brazo_izquierdo_arriba"}


class RosActuators:
    def __init__(self, node):
        self.node = node
        self.base = node.create_publisher(Twist, "/maxcim/base/cmd_vel", 1)
        self.arms = node.create_publisher(String, "/maxcim/arms/request", 1)
        self.feedback_at = self.base_at = self.arm_at = float("-inf")
        self.base_ok = False
        self.arm_status = {}
        self.pending_arm = None
        node.create_subscription(WheelFeedback, "/wheel_feedback", self.feedback, 1)
        node.create_subscription(String, "/nano_base/status", self.base_status, 1)
        node.create_subscription(String, "/maxcim/arms/state", self.arms_status, 1)

    def feedback(self, msg):
        if msg.status == 0 and msg.direction_valid:
            self.feedback_at = time.monotonic()
        else:
            self.feedback_at = float("-inf")

    def base_status(self, msg):
        try:
            state = json.loads(msg.data)
            self.base_ok = state["enabled"] and state["identified"]
            self.base_at = time.monotonic()
        except (ValueError, KeyError, TypeError):
            self.base_ok = False

    def arms_status(self, msg):
        try:
            self.arm_status = json.loads(msg.data)
            self.arm_at = time.monotonic()
            if self.arm_status.get("request_id") == self.pending_arm:
                self.pending_arm = None
        except (ValueError, TypeError):
            self.arm_status = {}

    def drive(self, linear, angular):
        msg = Twist()
        msg.linear.x, msg.angular.z = linear, angular
        self.base.publish(msg)

    def arm(self, action):
        if action not in ARMS:
            raise ValueError("Gesto fuera del catálogo")
        if time.monotonic()-self.arm_at > .5 or not self.arm_status.get("enabled"):
            raise ValueError("Brazos desconectados o calibración/habilitación pendiente")
        self.pending_arm = uuid.uuid4().hex
        msg = String()
        msg.data = json.dumps({"action": action, "id": self.pending_arm})
        self.arms.publish(msg)

    def stop(self):
        self.drive(0.0, 0.0)
        msg = String()
        msg.data = json.dumps({"action": "STOP", "id": uuid.uuid4().hex})
        self.arms.publish(msg)
        self.pending_arm = None

    def arm_busy(self):
        # Si un brazo desaparece durante una secuencia, bloquear nueva marcha.
        return bool(self.pending_arm or self.arm_status.get("busy", False))

    def ready(self):
        now = time.monotonic()
        ok = self.base_ok and now-self.base_at < .75 and now-self.feedback_at < .25
        return ok, "Nano sin habilitación o telemetría física reciente"


class Gateway(Node):
    def __init__(self):
        super().__init__("maxcim_gateway")
        self.declare_parameter("physical", False)
        self.physical = self.get_parameter("physical").value
        self.mode = "ros2-physical" if self.physical else "ros2-simulation"
        self.driver = RosActuators(self) if self.physical else SimulatedActuators()
        defaults = Limits(require_scan=self.physical)
        values = {}
        for key, value in defaults.__dict__.items():
            self.declare_parameter(key, value)
            values[key] = self.get_parameter(key).value
        if self.physical and not values["require_scan"]:
            raise ValueError("El gateway físico exige LiDAR")
        self.controller = Controller(self.driver, Limits(**values))
        if self.physical:
            self.controller.emergency()
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub = self.create_publisher(RobotState, "/maxcim/state", qos)
        self.create_subscription(LaserScan, "/scan", self.scan, qos_profile_sensor_data)
        self.create_service(ExecuteCommand, "/maxcim/execute_command", self.execute)
        self.create_service(ExecuteCommand, "/maxcim/voice_action", self.voice_action)
        self.timer = self.create_timer(.02, self.tick, clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.last_state = 0.0
        self.get_logger().info("Gateway iniciado: " + self.mode)

    def scan(self, msg):
        if not math.isfinite(msg.range_max) or msg.range_max <= 0 or not msg.ranges:
            self.controller.scan_at = float("-inf")
            return
        distances = [min(value, msg.range_max) for value in msg.ranges
                     if value == float("inf") or (math.isfinite(value) and msg.range_min <= value <= msg.range_max)]
        # Al menos 80% de rayos válidos; +inf indica sin retorno hasta range_max.
        if len(distances) / len(msg.ranges) < .8:
            self.controller.scan_at = float("-inf")
            return
        self.controller.scan(min(distances))

    def state(self):
        return dict(self.controller.state(), mode=self.mode, connected=True, position_feedback=False)

    def execute(self, req, response):
        try:
            if len(req.owner) > 160 or len(req.payload_json) > 8192:
                raise ValueError("Solicitud demasiado larga")
            payload = json.loads(req.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("Se requiere objeto de parámetros")
            dispatch(self.controller, req.owner, req.command, payload)
            response.accepted, response.message = True, "Aceptado por el gateway"
        except (ValueError, TypeError) as exc:
            response.accepted, response.message = False, str(exc)
        response.state_json = json.dumps(self.state(), allow_nan=False)
        return response

    def tick(self):
        self.controller.tick()
        if self.physical and (self.controller.linear or self.controller.angular) and not self.driver.ready()[0]:
            self.controller.stop()
            self.controller.reason = "Telemetría o habilitación de la base perdida"
        now = time.monotonic()
        if now-self.last_state >= .1:
            self.last_state = now
            if self.physical and not (self.controller.linear or self.controller.angular):
                self.driver.drive(0.0, 0.0)
            msg = RobotState()
            state = self.controller.state()
            for key, value in state.items():
                setattr(msg, key, "" if key == "owner" and value is None else value)
            msg.runtime_mode = self.mode
            self.pub.publish(msg)

    def voice_action(self, req, response):
        # El modelo no recibe el token ni puede adquirir/rearmar el robot.
        self.controller.tick()
        if not self.controller.owner or not self.controller.voice_enabled or req.command not in {"arm", "stop"}:
            response.accepted, response.message = False, "Gestos por voz no autorizados por el operador"
            response.state_json = json.dumps(self.state(), allow_nan=False)
            return response
        req.owner = self.controller.owner
        return self.execute(req, response)

    def destroy_node(self):
        self.controller.emergency()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = Gateway()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
