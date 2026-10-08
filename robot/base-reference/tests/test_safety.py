import unittest
from maxcim_base.safety import DriveGate, FeedbackClock


class SafetyTests(unittest.TestCase):
    def arm(self):
        g = DriveGate()
        g.feedback(1.0, 0)
        self.assertTrue(g.enable(1.0, True))
        return g

    def test_startup_and_calibration(self):
        g = DriveGate()
        self.assertIsNone(g.output(0))
        self.assertFalse(g.enable(0, True))
        g.feedback(0, 0)
        self.assertFalse(g.enable(0, False))

    def test_commands_before_arm_discarded(self):
        g = DriveGate()
        self.assertFalse(g.command(0, (100, 100)))
        g.feedback(1, 0)
        g.enable(1, True)
        self.assertEqual(g.output(1), (0, 0))

    def test_awaiting_first_command_stays_stopped(self):
        g = self.arm()
        g.feedback(4.0, 0)
        self.assertEqual(g.output(4.0), (0, 0))

    def test_timeout_never_rearms(self):
        g = self.arm()
        g.command(1, (20, 30))
        g.feedback(1.29, 0)
        self.assertEqual(g.output(1.29), (20, 30))
        self.assertIsNone(g.output(1.31))
        g.feedback(1.32, 0)
        self.assertFalse(g.command(1.32, (20, 30)))
        self.assertIsNone(g.output(1.32))

    def test_feedback_loss_stops(self):
        g = self.arm()
        g.command(1.24, (20, 30))
        self.assertIsNone(g.output(1.251))

    def test_all_faults_stop(self):
        for flag in (1, 2, 4, 8):
            g = self.arm()
            g.command(1, (20, 30))
            g.feedback(1.05, flag)
            self.assertIsNone(g.output(1.05))

    def test_bad_goal_stops(self):
        for goal in ((float('nan'), 1), (-1, 1), (1, float('inf'))):
            g = self.arm()
            self.assertFalse(g.command(1.1, goal))
            self.assertIsNone(g.output(1.1))

    def test_clock_wrap(self):
        c = FeedbackClock()
        self.assertEqual(c.accept(0xffffffff, 0xfffffff0, 1.0), 0)
        self.assertAlmostEqual(c.accept(0, 34, 1.05), 0)

    def test_duplicate_gap_and_old_queue(self):
        for seq, ms, now in ((1, 1050, 1.05), (2, 2000, 2), (2, 1050, 1.4)):
            c = FeedbackClock()
            c.accept(1, 1000, 1)
            with self.assertRaises(ValueError):
                c.accept(seq, ms, now)


if __name__ == '__main__':
    unittest.main()
