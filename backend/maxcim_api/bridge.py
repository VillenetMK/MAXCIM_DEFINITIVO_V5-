"""Contrato común: simulación local o servicios ROS 2 en la Raspberry."""
import asyncio
import json
import time

from maxcim_core.control import Controller, Limits
from maxcim_core.simulation import SimulatedActuators


def dispatch(controller, owner, command, payload):
    if command == "acquire":
        controller.acquire(owner)
    elif command == "heartbeat":
        controller.heartbeat(owner)
    elif command == "release":
        controller.release(owner)
    elif command == "drive":
        controller.drive(owner, payload.get("seq"), payload.get("linear"), payload.get("angular"))
    elif command == "arm":
        controller.arm(owner, payload.get("action"))
    elif command == "voice":
        controller._require_owner(owner)
        if type(payload.get("enabled")) is not bool:
            raise ValueError("Estado de voz inválido")
        controller.voice_enabled = payload["enabled"]
    elif command == "stop":
        controller.stop()
    elif command == "brake":
        controller.brake(owner, payload.get("seq"))
    elif command == "estop":
        controller.emergency()
    elif command == "reset":
        controller.reset()
    else:
        raise ValueError("Comando desconocido")


class SimulationBridge:
    mode = "simulation"

    def __init__(self):
        self.controller = Controller(SimulatedActuators(), Limits(require_scan=False))
        self.lock = asyncio.Lock()

    async def execute(self, owner, command, payload):
        async with self.lock:
            dispatch(self.controller, owner, command, payload)
            return self.state()

    def tick(self):
        self.controller.tick()

    def state(self):
        return dict(self.controller.state(), mode=self.mode, connected=True,
                    sensors={"lidar": "simulated", "camera": "not_connected", "audio": "not_connected"},
                    position_feedback=False)

    def close(self):
        self.controller.emergency()

    def camera(self):
        return None


class RosBridge:
    mode = "ros2"

    def __init__(self):
        import threading
        import rclpy
        from maxcim_interfaces.msg import RobotState
        from maxcim_interfaces.srv import ExecuteCommand
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.qos import QoSProfile, DurabilityPolicy
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CompressedImage
        from std_msgs.msg import UInt8MultiArray

        rclpy.init()
        self.rclpy, self.service_type = rclpy, ExecuteCommand
        self.node = rclpy.create_node("maxcim_web_bridge")
        self.client = self.node.create_client(ExecuteCommand, "/maxcim/execute_command")
        self.latest, self.received = {}, float("-inf")
        self.camera_frame, self.camera_at, self.audio_at = None, float("-inf"), float("-inf")
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.node.create_subscription(RobotState, "/maxcim/state", self._state, qos)
        self.node.create_subscription(CompressedImage, "/camera/image_raw/compressed", self._camera, qos_profile_sensor_data)
        self.node.create_subscription(UInt8MultiArray, "/audio/raw", self._audio, qos_profile_sensor_data)
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.thread.start()
        self.lock = asyncio.Lock()

    def _camera(self, msg):
        data = bytes(msg.data)
        if len(data) <= 2000000 and data.startswith(b"\xff\xd8"):
            self.camera_frame, self.camera_at = data, time.monotonic()

    def _audio(self, msg):
        if msg.data:
            self.audio_at = time.monotonic()

    def camera(self):
        return self.camera_frame if time.monotonic()-self.camera_at < 1.0 else None

    def _state(self, message):
        self.latest = {"owner": message.owner or None, "estop": message.estop,
            "linear": message.linear, "angular": message.angular,
            "arm_busy": message.arm_busy, "scan_recent": message.scan_recent,
            "clearance": message.clearance, "reason": message.reason,
            "voice_enabled": message.voice_enabled, "position_feedback": False}
        self.latest["sequence"] = message.sequence
        self.latest["mode"] = message.runtime_mode
        self.received = time.monotonic()

    async def execute(self, owner, command, payload):
        async with self.lock:
            if not self.client.service_is_ready():
                raise ValueError("Gateway ROS 2 no disponible; no se envió la orden")
            req = self.service_type.Request()
            req.owner, req.command, req.payload_json = owner, command, json.dumps(payload)
            pending = self.client.call_async(req)
            try:
                deadline = time.monotonic() + 1.0
                while not pending.done():
                    if time.monotonic() >= deadline:
                        raise ValueError("El gateway no confirmó la orden; comprueba el estado")
                    await asyncio.sleep(.01)
                response = pending.result()
                if response is None or not response.accepted:
                    raise ValueError(response.message if response else "Sin respuesta del gateway")
                return json.loads(response.state_json)
            finally:
                if not pending.done():
                    self.client.remove_pending_request(pending)
                    pending.cancel()

    def tick(self):
        pass  # La parada se aplica en el gateway, incluso si la API desaparece.

    def state(self):
        fresh = time.monotonic() - self.received < 1.0
        now = time.monotonic()
        return dict(self.latest, mode=self.latest.get("mode", self.mode), connected=fresh,
                    sensors={"lidar":"recent" if self.latest.get("scan_recent") else "stale",
                             "camera":"recent" if now-self.camera_at < 1.0 else "not_connected",
                             "audio":"recent" if now-self.audio_at < 1.0 else "not_connected"},
                    reason=self.latest.get("reason", "Esperando gateway") if fresh else "Gateway desconectado")

    def close(self):
        self.executor.shutdown(timeout_sec=2)
        self.thread.join(timeout=2)
        self.node.destroy_node()
        self.rclpy.shutdown()
