#!/usr/bin/env python3
"""ROS 2 integration test using a PTY, never a physical serial device.

Run after colcon build and sourcing install/setup.bash:
    python3 tests/ros_smoke.py

The Nano emulator speaks the real CRC/session protocol. Wheel counts change
ONLY through inject_ticks(), never as a consequence of a velocity command.
This verifies software integration; it does not validate an actual motor.
"""

import math
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tty

from maxcim_base.protocol import LineBuffer, decode, encode


class NanoEmulator:
    """A deterministic wire peer with explicitly injected measurement counts."""

    def __init__(self):
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        self.port = os.ttyname(self.slave)
        assert self.port.startswith('/dev/pts/')
        self.lock = threading.Lock()
        self.session = 0
        self.sequence = 0
        self.ack = 0
        self.left_ticks = self.right_ticks = 0
        self.telemetry_enabled = True
        self.commands = []
        self.error = None
        self.started = time.monotonic()
        self.shutdown = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _write(self, packet):
        # Packets are small enough for one PTY write; fail on an incomplete one.
        if os.write(self.master, packet) != len(packet):
            raise RuntimeError('Incomplete emulator write')

    def _run(self):
        lines = LineBuffer()
        next_sample = 0.0
        try:
            while not self.shutdown.is_set():
                ready, _, _ = select.select([self.master], [], [], 0.005)
                if ready:
                    for line in lines.feed(os.read(self.master, 4096)):
                        fields = decode(line).split()
                        with self.lock:
                            now = time.monotonic()
                            self.commands.append((now, fields))
                            if fields[0] == 'H':
                                self.session = int(fields[1])
                                self.ack = self.sequence = 0
                                self._write(encode(
                                    f'I {self.session} MAXCIM_BASE 1 0'))
                            elif fields[0] in ('S', 'V'):
                                if int(fields[1]) != self.session:
                                    raise AssertionError('Command has wrong session')
                                if int(fields[2]) <= self.ack:
                                    raise AssertionError('Command sequence repeated')
                                self.ack = int(fields[2])
                                if fields[0] == 'V':
                                    assert len(fields) == 5
                                    assert all(math.isfinite(float(v)) and float(v) >= 0
                                               for v in fields[3:])
                            else:
                                raise AssertionError('Unknown bridge command')
                now = time.monotonic()
                if now >= next_sample:
                    next_sample = now + 0.04
                    with self.lock:
                        if self.session and self.telemetry_enabled:
                            self.sequence += 1
                            uptime = int((now - self.started) * 1000)
                            self._write(encode(
                                f'T {self.session} {self.sequence} {uptime} '
                                f'{self.left_ticks} {self.right_ticks} '
                                f'0.0 0.0 0 0 0 {self.ack}'))
        except BaseException as exc:
            self.error = exc

    def inject_ticks(self, left, right):
        with self.lock:
            self.left_ticks += left
            self.right_ticks += right

    def set_telemetry(self, enabled):
        with self.lock:
            self.telemetry_enabled = enabled

    def corrupt_frame(self):
        with self.lock:
            packet = bytearray(encode(f'I {self.session} MAXCIM_BASE 1 0'))
            packet[-2] = ord('0') if packet[-2] != ord('0') else ord('1')
            self._write(bytes(packet))

    def since(self, start, kind=None):
        with self.lock:
            return [fields for when, fields in self.commands
                    if when >= start and (kind is None or fields[0] == kind)]

    def close(self):
        self.shutdown.set()
        self.thread.join(timeout=1.0)
        os.close(self.master)
        os.close(self.slave)


class Smoke:
    def __init__(self):
        # Imported here so the emulator can also be tested without ROS.
        import rclpy
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from std_srvs.srv import SetBool
        from tf2_msgs.msg import TFMessage

        self.rclpy = rclpy
        self.Twist = Twist
        self.SetBool = SetBool
        self.deadline = time.monotonic() + 35.0
        self.nano = NanoEmulator()
        self.children = []
        self.logs = []
        self.setpoint = None
        self.last_publish = 0.0
        self.odometry = []
        self.transforms = []
        self.node = None
        # Isolate discovery from other tests and machines on the network.
        os.environ.setdefault('ROS_DOMAIN_ID', str(30 + os.getpid() % 150))
        os.environ['ROS_LOCALHOST_ONLY'] = '1'
        rclpy.init()
        self.node = rclpy.create_node('maxcim_wire_smoke')
        self.publisher = self.node.create_publisher(Twist, '/cmd_vel', 10)
        self.node.create_subscription(Odometry, '/odom', self.odometry.append, 10)
        self.node.create_subscription(TFMessage, '/tf', self.on_tf, 10)
        self.enable = self.node.create_client(SetBool, '/nano_base/enable')

    def on_tf(self, message):
        self.transforms.extend(t for t in message.transforms
                               if t.header.frame_id == 'odom'
                               and t.child_frame_id == 'base_link')

    def start_nodes(self):
        parameters = {
            'calibration_confirmed': 'true',
            'left_wheel_radius_m': '0.05',
            'right_wheel_radius_m': '0.05',
            'wheel_separation_m': '0.3',
            'left_pulses_per_revolution': '100.0',
            'right_pulses_per_revolution': '100.0',
            'max_wheel_speed_mps': '1.0',
        }
        for module in ('maxcim_base.node', 'maxcim_odometry.node'):
            specific = dict(parameters)
            if module == 'maxcim_base.node':
                specific.update(serial_port=self.nano.port,
                                forward_direction_confirmed='true',
                                max_wheel_acceleration_mps2='10.0')
            args = [sys.executable, '-c', f'from {module} import main; main()',
                    '--ros-args']
            for name, value in specific.items():
                args.extend(['-p', f'{name}:={value}'])
            log = tempfile.TemporaryFile(mode='w+t')
            self.logs.append(log)
            self.children.append(subprocess.Popen(
                args, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, env=os.environ.copy()))

    def step(self):
        now = time.monotonic()
        if now >= self.deadline:
            raise AssertionError('Integration test exceeded 35 seconds')
        if self.nano.error is not None:
            raise AssertionError('Nano emulator failed') from self.nano.error
        for child in self.children:
            if child.poll() is not None:
                raise AssertionError(f'ROS node exited with {child.returncode}')
        if self.setpoint is not None and now - self.last_publish >= 0.04:
            message = self.Twist()
            message.linear.x, message.angular.z = self.setpoint
            self.publisher.publish(message)
            self.last_publish = now
        self.rclpy.spin_once(self.node, timeout_sec=0.01)

    def wait(self, predicate, description, timeout=3.0):
        until = min(self.deadline, time.monotonic() + timeout)
        while time.monotonic() < until:
            self.step()
            if predicate():
                return
        raise AssertionError('Timed out: ' + description)

    def drain(self, duration):
        until = time.monotonic() + duration
        while time.monotonic() < until:
            self.step()

    def arm(self):
        request = self.SetBool.Request()
        request.data = True
        future = self.enable.call_async(request)
        self.wait(future.done, 'enable response')
        response = future.result()
        assert response.success, response.message

    def expect_drive(self, after, direction=None):
        def matches():
            for fields in self.nano.since(after, 'V'):
                left, right = map(float, fields[3:])
                if direction == 'left' and 0 <= left < right:
                    return True
                if direction == 'right' and 0 <= right < left:
                    return True
                if direction is None and left > 0 and right > 0:
                    return True
            return False
        self.wait(matches, 'forward wheel velocity command')

    def run(self):
        self.start_nodes()
        self.wait(lambda: self.enable.service_is_ready() and len(self.odometry) >= 2
                  and self.transforms and self.publisher.get_subscription_count() > 0,
                  'ROS discovery, handshake, baseline odometry and TF', timeout=15.0)
        assert not self.nano.since(0, 'V'), 'Motor command before explicit enabling'

        self.arm()
        self.setpoint = (0.08, 0.0)
        start = time.monotonic()
        self.expect_drive(start)
        self.drain(0.35)
        assert all(abs(msg.pose.pose.position.x) < 1e-12 for msg in self.odometry), (
            'cmd_vel generated odometry without measured wheel counts')

        self.nano.inject_ticks(10, 10)
        expected = 10 * 2 * math.pi * 0.05 / 100
        self.wait(lambda: abs(self.odometry[-1].pose.pose.position.x - expected) < 1e-8,
                  '10 explicit physical pulses determine traveled distance')
        self.wait(lambda: abs(self.transforms[-1].transform.translation.x - expected) < 1e-8,
                  'TF agrees with measured odometry')
        assert self.odometry[-1].header.frame_id == 'odom'
        assert self.odometry[-1].child_frame_id == 'base_link'

        for angular, direction in ((0.2, 'left'), (-0.2, 'right')):
            start = time.monotonic()
            self.setpoint = (0.08, angular)
            self.expect_drive(start, direction)

        # An impossible forward-only maneuver must stop and latch disabled.
        start = time.monotonic()
        self.setpoint = (-0.05, 0.0)
        self.wait(lambda: bool(self.nano.since(start, 'S')), 'reverse command stops')
        self.setpoint = (0.08, 0.0)
        start = time.monotonic()
        self.drain(0.35)
        assert not self.nano.since(start, 'V'), 'Reverse rejection failed to latch stop'
        self.arm()
        self.expect_drive(time.monotonic())

        # Loss of the command publisher must also require an explicit re-arm.
        self.setpoint = None
        start = time.monotonic()
        self.wait(lambda: bool(self.nano.since(start, 'S')), 'command timeout stops', 1.0)
        self.setpoint = (0.08, 0.0)
        start = time.monotonic()
        self.drain(0.35)
        assert not self.nano.since(start, 'V'), 'Old enable survives command timeout'
        self.arm()
        self.expect_drive(time.monotonic())

        # Corrupt data must force a new verified session and leave drive off.
        start = time.monotonic()
        self.nano.corrupt_frame()
        self.wait(lambda: bool(self.nano.since(start, 'H')), 'CRC fault renews session')
        self.drain(0.20)
        start = time.monotonic()
        self.drain(0.15)
        assert not self.nano.since(start, 'V'), 'CRC reset preserved motor enable'
        self.arm()
        self.expect_drive(time.monotonic())

        # No incoming measurements means no new odometry or TF publication.
        start = time.monotonic()
        self.nano.set_telemetry(False)
        self.wait(lambda: bool(self.nano.since(start, 'S')), 'serial timeout stops', 1.0)
        self.drain(0.35)  # Drain already transported DDS samples.
        counts = (len(self.odometry), len(self.transforms))
        self.drain(0.35)
        assert counts == (len(self.odometry), len(self.transforms)), (
            'Odometry or TF is restamped without fresh Nano telemetry')
        assert abs(self.odometry[-1].pose.pose.position.x - expected) < 1e-8
        print('PASS: CRC/session, explicit enable, forward turns, measured odometry/TF, '
              'reverse rejection, command watchdog and stale-serial stop')

    def close(self):
        self.setpoint = None
        for child in self.children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGINT)
        for child in self.children:
            try:
                child.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=1.0)
        if self.node is not None:
            self.node.destroy_node()
        if self.rclpy.ok():
            self.rclpy.shutdown()
        self.nano.close()
        for log in self.logs:
            log.close()

    def dump_logs(self):
        for index, log in enumerate(self.logs):
            log.flush()
            log.seek(0)
            print(f'--- ROS child {index} ---', file=sys.stderr)
            print(log.read(), file=sys.stderr)


def main():
    smoke = Smoke()
    try:
        smoke.run()
    except BaseException:
        smoke.dump_logs()
        raise
    finally:
        smoke.close()


if __name__ == '__main__':
    main()
