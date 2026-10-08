import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from core import Controller, ControlError


class DeadmanTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.c = Controller(clock=lambda: self.now)
        self.c.odom_at = self.c.scan_at = self.now
        self.c.acquire('one')

    def drive(self, seq=1, linear=.05, angular=0):
        self.c.command('one', seq, self.c.challenge('one'), linear, angular)

    def test_deadman_stops_without_browser_or_network(self):
        self.drive()
        self.assertEqual(self.c.tick(), (.05, 0))
        self.now += .31
        self.assertEqual(self.c.tick(), (0, 0))
        for _ in range(4): self.c.tick()
        self.assertIsNone(self.c.tick())

    def test_release_invalidates_buffered_commands(self):
        old = self.c.challenge('one')
        self.drive()
        self.c.stop()
        with self.assertRaises(ControlError): self.c.command('one', 2, old, .05, 0)
        self.assertEqual(self.c.tick(), (0, 0))

    def test_old_challenge_and_duplicate_sequence_rejected(self):
        old = self.c.challenge('one')
        self.now += .36
        with self.assertRaises(ControlError): self.c.command('one', 1, old, .05, 0)
        self.drive(seq=2)
        with self.assertRaises(ControlError): self.drive(seq=2)

    def test_stale_odom_or_scan_blocks_motion(self):
        for sensor in ['odom_at', 'scan_at']:
            with self.subTest(sensor=sensor):
                self.c.odom_at = self.c.scan_at = self.now
                setattr(self.c, sensor, self.now - 2)
                with self.assertRaises(ControlError): self.drive(seq=self.c.sequence+1)
                self.assertEqual(self.c.tick(), (0, 0))

    def test_sensor_loss_stops_an_accepted_command(self):
        self.drive()
        self.c.scan_at -= 2
        self.assertEqual(self.c.tick(), (0, 0))

    def test_competing_controller_and_second_operator_blocked(self):
        with self.assertRaises(ControlError): self.c.acquire('two')
        self.drive()
        self.c.other_controller = True
        self.assertEqual(self.c.tick(), (0, 0))
        with self.assertRaises(ControlError): self.drive(seq=2)

    def test_disconnect_and_reconnect_never_resume(self):
        self.drive()
        self.c.release('one')
        self.assertEqual(self.c.tick(), (0, 0))
        self.c.acquire('two')
        self.assertEqual(self.c.target, (0, 0))
        with self.assertRaises(ControlError): self.c.command('one', 2, '', .05, 0)

    def test_invalid_values_never_reach_robot(self):
        for linear, angular in [(math.nan,0),(math.inf,0),(.16,0),(0,.41),(True,0),('0.05',0)]:
            with self.subTest(linear=linear,angular=angular):
                with self.assertRaises(ControlError): self.drive(seq=self.c.sequence+1,linear=linear,angular=angular)
                self.assertEqual(self.c.tick(), (0, 0))


if __name__ == '__main__': unittest.main()
