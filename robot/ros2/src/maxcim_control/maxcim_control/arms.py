"""Secuenciador acotado para firmware ESP32 V5; nunca carga SERV.py."""
import json
import time
from collections import deque
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.clock import Clock, ClockType
from std_msgs.msg import String
from std_srvs.srv import SetBool
import serial
from maxcim_core.catalog import Catalog, calibrated_moves


class Arms(Node):
    def __init__(self):
        super().__init__("maxcim_arms")
        for key, value in {"serial_port":"", "calibration_file":"", "catalog_dir":""}.items():
            self.declare_parameter(key, value)
        p = lambda key: self.get_parameter(key).value
        if not all(p(key) for key in ("serial_port","calibration_file","catalog_dir")):
            raise ValueError("Configura puerto by-id, calibración y catálogo de brazos")
        self.calibration = json.loads(Path(p("calibration_file")).read_text())
        self.catalog = Catalog(p("catalog_dir"))
        self.port = serial.Serial(p("serial_port"),115200,timeout=0,write_timeout=.03,exclusive=True)
        self.port.reset_input_buffer()
        self.buffer = bytearray()
        self.groups = deque()
        self.enabled = False
        self.pending_enable = False
        self.identified = False
        self.feedback_at = float("-inf")
        self.positions = {}
        self.firmware_busy = False
        self.awaiting = False
        self.group_id = 0
        self.wait_after = 0.0
        self.last_poll = self.next_group = 0.0
        self.last_request = ""
        self.reason = "Esperando firmware MAXCIM_ARMS 5"
        self.pub = self.create_publisher(String,"/maxcim/arms/state",1)
        self.create_subscription(String,"/maxcim/arms/request",self.request,1)
        self.create_service(SetBool,"~/enable",self.enable)
        self.timer = self.create_timer(.02,self.tick,clock=Clock(clock_type=ClockType.STEADY_TIME))

    def write(self, line):
        data = (line+"\n").encode("ascii")
        if self.port.write(data) != len(data):
            raise serial.SerialException("Escritura incompleta")

    def stop(self, reason):
        self.groups.clear()
        self.awaiting = False
        self.reason = reason
        if self.port.is_open:
            self.write("STOP")

    def enable(self, req, response):
        try:
            self.stop("Brazos deshabilitados")
            self.enabled = self.pending_enable = False
            if not req.data:
                self.write("DISABLE")
            else:
                if not self.identified or time.monotonic()-self.feedback_at > .3:
                    raise ValueError("ESP32 sin identidad o respuesta reciente")
                calibrated_moves(self.catalog.gesture("home"), self.calibration)
                for channel in (0,1,2,4,5,6):
                    config = self.calibration["servos"][str(channel)]
                    pulse = config.get("home_pulse")
                    if type(pulse) is not int or not config["pulse_min"] <= pulse <= config["pulse_max"]:
                        raise ValueError("Posición inicial sin calibrar")
                # Requiere que el brazo se haya colocado físicamente en la posición
                # inicial indicada. INIT no conoce la posición real del servo.
                for channel in (0,1,2,4,5,6):
                    self.write(f"INIT {channel} {self.calibration['servos'][str(channel)]['home_pulse']}")
                self.pending_enable = True
                self.write("ENABLE")
            response.success, response.message = True, "Solicitud enviada; consulta /maxcim/arms/state"
        except (ValueError, KeyError, OSError, serial.SerialException) as exc:
            self.enabled = self.pending_enable = False
            response.success, response.message = False, str(exc)
        return response

    def request(self, msg):
        try:
            if len(msg.data) > 1024:
                raise ValueError("Orden demasiado larga")
            data = json.loads(msg.data)
            self.last_request = str(data.get("id", ""))[:80]
            if data.get("action") == "STOP":
                self.stop("Secuencia cancelada; mantener posición mandada")
                return
            if not self.enabled or time.monotonic()-self.feedback_at > .3:
                raise ValueError("Brazos no habilitados o sin respuesta")
            if self.groups or self.awaiting or self.firmware_busy:
                raise ValueError("Brazos ocupados")
            groups = calibrated_moves(self.catalog.gesture(data["action"]), self.calibration)
            self.groups.extend(groups)
            self.reason = "Secuencia en curso"
        except (ValueError, TypeError, KeyError, OSError, serial.SerialException) as exc:
            self.reason = str(exc)

    def receive(self, line):
        if line == "MAXCIM_ARMS 5":
            self.identified = True
        elif line.startswith("STATE ") and self.identified:
            parts = line.split()
            if len(parts) != 10:
                raise ValueError("Respuesta ESP32 inválida")
            enabled, busy = int(parts[1]), int(parts[2])
            group_id = int(parts[3])
            ticks = [int(v) for v in parts[4:]]
            if enabled not in (0,1) or busy not in (0,1) or any(not 0 <= t <= 600 for t in ticks):
                raise ValueError("Telemetría ESP32 fuera de rango")
            self.feedback_at = time.monotonic()
            self.positions = dict(zip((0,1,2,4,5,6),ticks))
            self.firmware_busy = bool(busy)
            if self.pending_enable and enabled:
                self.enabled = bool(enabled)
                self.pending_enable = False
            elif not enabled:
                self.enabled = False
            # Una respuesta PING antigua no confirma el nuevo grupo.
            if group_id == self.group_id:
                self.awaiting = False
                if not busy and self.wait_after:
                    self.next_group = time.monotonic()+self.wait_after
                    self.wait_after = 0.0
        elif line.startswith("ERR"):
            self.stop(line)
            self.enabled = False

    def tick(self):
        now = time.monotonic()
        try:
            count = self.port.in_waiting
            if count > 2048 or len(self.buffer)+count > 4096:
                raise ValueError("Cola ESP32 excesiva")
            self.buffer.extend(self.port.read(count))
            while b"\n" in self.buffer:
                line,_,self.buffer = self.buffer.partition(b"\n")
                self.receive(line.decode("ascii").strip())
            if self.enabled and now-self.feedback_at > .3:
                self.stop("ESP32 sin respuesta; watchdog físico detiene")
                self.enabled = False
                self.write("DISABLE")
            if now-self.last_poll >= .1:
                self.last_poll = now
                self.write("PING" if self.identified else "HELLO")
            if self.enabled and self.groups and not self.awaiting and not self.firmware_busy and now >= self.next_group:
                group = self.groups.popleft()
                delay = 0.0
                for step in group:
                    if "wait" in step:
                        delay = max(delay,step["wait"])
                    else:
                        self.write(f"M {step['channel']} {step['ticks']} {round(step['interval'])}")
                        delay = max(delay,step["delay"])
                self.wait_after = delay
                self.group_id += 1
                self.awaiting = True
                self.write(f"G {self.group_id}")
            state = {"enabled":self.enabled,"busy":bool(self.groups or self.awaiting or self.firmware_busy),
                     "request_id":self.last_request,"reason":self.reason,"position_feedback":False}
            msg = String();msg.data = json.dumps(state);self.pub.publish(msg)
        except (ValueError, UnicodeError, OSError, serial.SerialException) as exc:
            self.groups.clear();self.enabled = False
            self.get_logger().error(str(exc))
            try:
                self.write("STOP");self.write("DISABLE")
            except (OSError,serial.SerialException):
                pass
            self.timer.cancel();self.port.close()

    def destroy_node(self):
        if hasattr(self,"port"):
            try:
                self.stop("Cierre; mantener último PWM")
            except (OSError,serial.SerialException):
                pass
            self.port.close()
        return super().destroy_node()


def main(args=None):
    rclpy.init(args=args);node = None
    try:
        node = Arms();rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node:node.destroy_node()
        if rclpy.ok():rclpy.shutdown()
