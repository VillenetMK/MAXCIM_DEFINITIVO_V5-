"""Exercise the actual bridge callbacks without ROS or serial hardware.

Only the NanoBase class is loaded through AST, with transport/message doubles.
The separate ros_smoke.py covers real rclpy, generated messages, DDS and PTYs.
"""

import ast
import math
from pathlib import Path
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from maxcim_base.kinematics import Geometry, wheel_targets
from maxcim_base.protocol import (
    LineBuffer, decode, encode, parse_identity, parse_telemetry,
)
from maxcim_base.safety import DriveGate, FeedbackClock


class Port:
    def __init__(self):
        self.is_open = True
        self.incoming = b''
        self.sent = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def read(self, count):
        result, self.incoming = self.incoming[:count], self.incoming[count:]
        return result

    def write(self, packet):
        self.sent.append(decode(packet))
        return len(packet)

    def reset_input_buffer(self):
        self.incoming = b''

    def close(self):
        self.is_open = False


def feedback_message():
    return SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace()))


def load_bridge():
    filename = Path(__file__).parents[2] / 'ros2/src/maxcim_base/maxcim_base/node.py'
    tree = ast.parse(filename.read_text(), filename=str(filename))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == 'NanoBase')
    namespace = {
        'Node': object, 'time': SimpleNamespace(monotonic=lambda: 10.0),
        'isfinite': math.isfinite, 'secrets': secrets,
        'serial': SimpleNamespace(SerialException=OSError),
        'Geometry': Geometry, 'wheel_targets': wheel_targets,
        'encode': encode, 'decode': decode, 'parse_identity': parse_identity,
        'parse_telemetry': parse_telemetry, 'LineBuffer': LineBuffer,
        'DriveGate': DriveGate, 'FeedbackClock': FeedbackClock,
        'WheelFeedback': feedback_message, 'String': SimpleNamespace,
    }
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(filename), 'exec'), namespace)
    return namespace['NanoBase']


class BridgeCallbacks(unittest.TestCase):
    def setUp(self):
        cls = load_bridge()
        self.node = cls.__new__(cls)
        n = self.node
        n.port = Port()
        n.geometry = Geometry(0.05, 0.05, 0.3, 100.0, 100.0, 1.0)
        n.forward_confirmed = True
        n.acceleration = 1.0
        n.gate = DriveGate()
        n.gate.feedback(9.99, 0)
        n.clock_tracker = FeedbackClock()
        n.base_frame = 'base_link'
        n.session = 123
        n.command_seq = n.last_ack = 0
        n.identified = True
        n.ack_at = 10.0
        n.last_hello = -10.0
        n.last_write = 9.94
        n.last_status = 10.0
        n.applied = (0.0, 0.0)
        n.lines = LineBuffer()
        n.publisher = Mock()
        n.status_pub = Mock()
        n.timer = Mock()
        n.get_logger = lambda: Mock()
        n.get_clock = lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(nanoseconds=10000000000))

    def command(self, linear, angular=0.0):
        self.node.on_command(SimpleNamespace(
            linear=SimpleNamespace(x=linear, y=0.0, z=0.0),
            angular=SimpleNamespace(x=0.0, y=0.0, z=angular)))

    def arm(self):
        response = self.node.on_enable(SimpleNamespace(data=True), SimpleNamespace())
        self.assertTrue(response.success, response.message)

    def test_command_before_enable_is_never_replayed(self):
        self.command(0.08)
        self.arm()
        self.node.poll()
        self.assertTrue(self.node.port.sent[-1].startswith('S '))
        self.command(0.08)
        self.node.last_write = 9.94
        self.node.poll()
        self.assertTrue(self.node.port.sent[-1].startswith('V '))
        self.node.publisher.publish.assert_not_called()

    def test_reverse_stops_and_positive_command_cannot_rearm(self):
        self.arm()
        self.command(0.08)
        self.command(-0.05)
        self.assertFalse(self.node.gate.enabled)
        self.assertTrue(self.node.port.sent[-1].startswith('S '))
        self.command(0.08)
        self.node.poll()
        self.assertFalse(any(packet.startswith('V ') for packet in self.node.port.sent))

    def test_expired_command_stops_even_with_fresh_telemetry(self):
        self.arm()
        self.node.gate.command(9.60, (10.0, 10.0))
        self.node.poll()
        self.assertFalse(self.node.gate.enabled)
        self.assertTrue(self.node.port.sent[-1].startswith('S '))
        self.command(0.08)
        self.assertFalse(self.node.gate.enabled)

    def test_only_verified_telemetry_publishes_wheel_measurements(self):
        self.node.receive(encode('T 123 1 100 7 11 0.0 0.0 0 0 0 0'), 10.0)
        msg = self.node.publisher.publish.call_args.args[0]
        self.assertEqual((msg.left_ticks, msg.right_ticks), (7, 11))
        self.assertEqual(msg.header.frame_id, 'base_link')
        self.assertTrue(msg.direction_valid)
        with self.assertRaises(ValueError):
            self.node.receive(encode('T 123 1 100 99 99 0.0 0.0 0 0 0 0'), 10.01)
        with self.assertRaises(ValueError):
            self.node.receive(encode('T 124 2 140 99 99 0.0 0.0 0 0 0 0'), 10.04)
        self.assertEqual(self.node.publisher.publish.call_count, 1)

    def test_fault_flag_stops_and_invalidates_direction(self):
        self.arm()
        self.node.receive(encode('T 123 1 100 7 11 0.0 0.0 0 0 4 0'), 10.0)
        self.assertFalse(self.node.gate.enabled)
        self.assertFalse(self.node.publisher.publish.call_args.args[0].direction_valid)
        response = self.node.on_enable(SimpleNamespace(data=True), SimpleNamespace())
        self.assertFalse(response.success)

    def test_serial_age_and_crc_fault_both_reset_and_latch_stop(self):
        for cause in ('stale', 'crc'):
            with self.subTest(cause=cause):
                self.setUp()
                self.arm()
                self.command(0.08)
                previous_session = self.node.session
                if cause == 'stale':
                    self.node.gate.feedback_at = 9.0
                else:
                    self.node.port.incoming = b'T corrupt*0000\n'
                self.node.poll()
                self.assertFalse(self.node.gate.enabled)
                self.assertNotEqual(self.node.session, previous_session)
                self.assertTrue(any(p.startswith('S ') for p in self.node.port.sent))
                self.node.publisher.publish.assert_not_called()


if __name__ == '__main__':
    unittest.main()
