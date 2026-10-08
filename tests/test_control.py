import unittest
from maxcim_core.control import Controller, Limits, Rejected
from maxcim_core.simulation import SimulatedActuators

class Clock:
    now = 10.0
    def __call__(self): return self.now

class ControlTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.driver = SimulatedActuators()
        self.c = Controller(self.driver, Limits(), self.clock)
        self.c.scan(2.0)
        self.c.acquire("one")

    def test_other_operator_cannot_take_or_move(self):
        for action in (lambda:self.c.acquire("two"),lambda:self.c.drive("two",1,.1,0)):
            with self.assertRaises(Rejected): action()

    def test_missing_owner_never_authorizes_motion(self):
        self.c.release("one")
        with self.assertRaises(Rejected): self.c.drive(None,1,.1,0)

    def test_stale_command_cannot_restart_after_brake(self):
        self.c.drive("one",1,.1,0)
        self.c.brake("one",3)
        with self.assertRaises(Rejected): self.c.drive("one",2,.1,0)
        self.assertEqual(self.c.linear,0)
        self.c.drive("one",4,.1,0)

    def test_public_stop_revokes_late_commands(self):
        self.c.drive("one",1,.1,0);self.c.stop()
        with self.assertRaises(Rejected):self.c.drive("one",2,.1,0)

    def test_command_expiry_even_with_operator_heartbeat(self):
        self.c.drive("one",1,.1,0)
        self.clock.now += .36;self.c.heartbeat("one")
        self.assertEqual(self.c.linear,0)

    def test_operator_expiry_cancels_voice_and_motion(self):
        self.c.voice_enabled=True;self.clock.now+=2.1;self.c.tick()
        self.assertIsNone(self.c.owner);self.assertFalse(self.c.voice_enabled)

    def test_obstacle_stops_active_motion(self):
        self.c.drive("one",1,.1,0);self.c.scan(.2);self.c.tick()
        self.assertEqual(self.c.linear,0)

    def test_missing_scan_rejects_motion(self):
        self.clock.now+=.51
        with self.assertRaises(Rejected):self.c.drive("one",1,.1,0)

    def test_nan_bool_infinity_and_limits_rejected(self):
        for value in (float("nan"),float("inf"),True,.21):
            with self.assertRaises(Rejected):self.c.drive("one",1,value,0)

    def test_emergency_requires_reset_and_new_acquire(self):
        self.c.emergency()
        with self.assertRaises(Rejected):self.c.acquire("one")
        self.c.reset()
        with self.assertRaises(Rejected):self.c.drive("one",1,.1,0)
        self.c.acquire("one");self.c.drive("one",1,.1,0)

    def test_arm_cannot_start_on_moving_base(self):
        self.c.drive("one",1,.1,0)
        with self.assertRaises(Rejected):self.c.arm("one","saludar")

    def test_base_waits_for_arm_completion(self):
        self.c.arm("one","saludar")
        with self.assertRaises(Rejected):self.c.drive("one",1,.1,0)
        self.c.stop();self.assertFalse(self.driver.arm_busy())
