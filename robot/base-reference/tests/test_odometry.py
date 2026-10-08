"""Numerical and failure-path tests without ROS, serial ports or a robot."""

from dataclasses import replace
import math
import unittest

from maxcim_odometry.core import Calibration, Sample, UINT32_MASK, WheelOdometry


class OdometryTests(unittest.TestCase):
    def setUp(self):
        # This synthetic test calibration gives exactly 1 mm / pulse.
        self.calibration = Calibration(1 / (2 * math.pi), 1 / (2 * math.pi), 0.4, 1000, 1000)
        self.odom = WheelOdometry(self.calibration)
        self.first = Sample(123, 0, 1000, 0, 0, 10_000_000_000)
        self.assertIsNone(self.odom.accept(self.first, self.first.stamp_ns))

    def sample(self, sequence=1, left=10, right=10, **kwargs):
        result = replace(
            self.first, sequence=sequence, uptime_ms=1000 + sequence * 50,
            left_ticks=left, right_ticks=right,
            stamp_ns=self.first.stamp_ns + sequence * 50_000_000,
        )
        return replace(result, **kwargs)

    def accept(self, sample):
        return self.odom.accept(sample, sample.stamp_ns)

    def test_straight_motion_uses_counts(self):
        result = self.accept(self.sample())
        self.assertAlmostEqual(result.x, 0.01)
        self.assertAlmostEqual(result.y, 0.0)
        self.assertAlmostEqual(result.yaw, 0.0)
        self.assertAlmostEqual(result.linear_velocity, 0.2)
        self.assertAlmostEqual(result.angular_velocity, 0.0)

    def test_left_arc_exact_se2(self):
        result = self.accept(self.sample(left=10, right=30))
        theta = 0.02 / 0.4
        radius = 0.02 / theta
        self.assertAlmostEqual(result.x, radius * math.sin(theta))
        self.assertAlmostEqual(result.y, radius * (1 - math.cos(theta)))
        self.assertAlmostEqual(result.yaw, theta)

    def test_right_arc_exact_se2(self):
        result = self.accept(self.sample(left=30, right=10))
        theta = -0.02 / 0.4
        radius = 0.02 / theta
        self.assertAlmostEqual(result.x, radius * math.sin(theta))
        self.assertAlmostEqual(result.y, radius * (1 - math.cos(theta)))
        self.assertAlmostEqual(result.yaw, theta)

    def test_pivot_about_stopped_left_wheel(self):
        result = self.accept(self.sample(left=0, right=40))
        self.assertAlmostEqual(result.yaw, 0.1)
        self.assertAlmostEqual(result.x, 0.2 * math.sin(0.1))
        self.assertAlmostEqual(result.y, 0.2 * (1 - math.cos(0.1)))
        # Forward-only pivot is about a wheel, never a zero-radius center spin.
        self.assertGreater(result.linear_velocity, 0)

    def test_pivot_about_stopped_right_wheel(self):
        result = self.accept(self.sample(left=40, right=0))
        self.assertLess(result.yaw, 0)
        self.assertGreater(result.x, 0)
        self.assertLess(result.y, 0)

    def test_integration_uses_current_orientation(self):
        first = self.accept(self.sample(left=0, right=40))
        second = self.accept(self.sample(sequence=2, left=10, right=50))
        self.assertAlmostEqual(second.x - first.x, 0.01 * math.cos(first.yaw))
        self.assertAlmostEqual(second.y - first.y, 0.01 * math.sin(first.yaw))

    def test_asymmetric_radii_are_independent(self):
        calibration = replace(self.calibration, left_wheel_radius_m=1 / math.pi)
        odom = WheelOdometry(calibration)
        odom.accept(self.first, self.first.stamp_ns)
        sample = self.sample(left=5, right=10)
        result = odom.accept(sample, sample.stamp_ns)
        self.assertAlmostEqual(result.x, 0.01)
        self.assertAlmostEqual(result.yaw, 0.0)

    def test_asymmetric_pulse_calibration(self):
        odom = WheelOdometry(replace(self.calibration, left_pulses_per_revolution=2000))
        odom.accept(self.first, self.first.stamp_ns)
        sample = self.sample(left=20, right=10)
        result = odom.accept(sample, sample.stamp_ns)
        self.assertAlmostEqual(result.x, 0.01)
        self.assertAlmostEqual(result.yaw, 0.0)

    def test_zero_count_delta_reports_measured_stop(self):
        result = self.accept(self.sample(left=0, right=0))
        self.assertEqual((result.x, result.y, result.yaw), (0, 0, 0))
        self.assertEqual((result.linear_velocity, result.angular_velocity), (0, 0))

    def test_uint32_tick_sequence_and_uptime_wrap(self):
        odom = WheelOdometry(self.calibration)
        baseline = replace(
            self.first, sequence=UINT32_MASK, uptime_ms=UINT32_MASK - 20,
            left_ticks=UINT32_MASK - 4, right_ticks=UINT32_MASK - 4,
        )
        odom.accept(baseline, baseline.stamp_ns)
        sample = self.sample(sequence=0, uptime_ms=29, left=5, right=5,
                             stamp_ns=baseline.stamp_ns + 50_000_000)
        result = odom.accept(sample, sample.stamp_ns)
        self.assertAlmostEqual(result.dt, 0.05)
        self.assertAlmostEqual(result.x, 0.01)

    def test_session_reset_preserves_pose_and_rejects_retired_session(self):
        original = self.accept(self.sample())
        reset = self.sample(sequence=0, session=456, left=10000, right=20000,
                            uptime_ms=0, stamp_ns=self.first.stamp_ns + 100_000_000)
        self.assertIsNone(self.accept(reset))
        self.assertEqual(self.odom.x, original.x)
        retired = self.sample(sequence=2, stamp_ns=reset.stamp_ns + 10_000_000)
        self.assertIsNone(self.accept(retired))
        self.assertEqual(self.odom.last_reason, "retired_session")
        sample = replace(reset, sequence=1, left_ticks=10010, right_ticks=20010,
                         uptime_ms=50, stamp_ns=reset.stamp_ns + 50_000_000)
        result = self.accept(sample)
        self.assertAlmostEqual(result.x, 0.02)

    def test_duplicate_does_not_change_pose_or_baseline(self):
        first = self.sample()
        self.accept(first)
        self.assertIsNone(self.accept(replace(first, stamp_ns=first.stamp_ns + 1)))
        self.assertEqual(self.odom.last_reason, "old_sequence")
        second = self.accept(self.sample(sequence=2, left=20, right=20))
        self.assertAlmostEqual(second.x, 0.02)

    def test_out_of_order_sequence_rejected(self):
        self.accept(self.sample())
        self.assertIsNone(self.accept(self.sample(sequence=0, stamp_ns=10_060_000_000)))
        self.assertEqual(self.odom.last_reason, "old_sequence")
        result = self.accept(self.sample(sequence=2, left=20, right=20))
        self.assertAlmostEqual(result.x, 0.02)

    def test_sequence_gap_rebaselines_without_inventing_travel(self):
        self.assertIsNone(self.accept(self.sample(sequence=2, left=20, right=20)))
        self.assertEqual(self.odom.last_reason, "sequence_gap")
        result = self.accept(self.sample(sequence=3, left=30, right=30))
        self.assertAlmostEqual(result.x, 0.01)

    def test_stale_sample_cannot_publish_or_bridge_unknown_interval(self):
        old = self.sample()
        self.assertIsNone(self.odom.accept(old, old.stamp_ns + 300_000_000))
        self.assertEqual(self.odom.last_reason, "stale_feedback")
        self.assertIsNone(self.accept(self.sample(sequence=2, left=20, right=20)))
        result = self.accept(self.sample(sequence=3, left=30, right=30))
        self.assertAlmostEqual(result.x, 0.01)

    def test_future_timestamp_rejected(self):
        sample = self.sample()
        self.assertIsNone(self.odom.accept(sample, sample.stamp_ns - 1))
        self.assertEqual(self.odom.last_reason, "future_stamp")

    def test_duplicate_timestamp_rejected(self):
        self.assertIsNone(self.accept(self.sample(stamp_ns=self.first.stamp_ns)))
        self.assertEqual(self.odom.last_reason, "non_increasing_stamp")

    def test_mcu_time_defines_velocity_not_host_delivery_jitter(self):
        sample = self.sample(stamp_ns=self.first.stamp_ns + 90_000_000)
        result = self.accept(sample)
        self.assertAlmostEqual(result.dt, 0.05)
        self.assertAlmostEqual(result.linear_velocity, 0.2)

    def test_long_mcu_interval_rebaselines(self):
        self.assertIsNone(self.accept(self.sample(uptime_ms=1500)))
        self.assertEqual(self.odom.last_reason, "sample_gap")
        sample = self.sample(sequence=2, left=20, right=20, uptime_ms=1550)
        result = self.accept(sample)
        self.assertAlmostEqual(result.x, 0.01)

    def test_long_host_interval_rebaselines(self):
        self.assertIsNone(self.accept(self.sample(stamp_ns=10_500_000_000)))
        self.assertEqual(self.odom.last_reason, "sample_gap")

    def test_mcu_clock_restarts_without_pose_jump(self):
        self.assertIsNone(self.accept(self.sample(uptime_ms=1)))
        self.assertEqual(self.odom.last_reason, "invalid_mcu_interval")
        result = self.accept(self.sample(sequence=2, left=20, right=20, uptime_ms=51))
        self.assertAlmostEqual(result.x, 0.01)

    def test_zero_mcu_interval_rejected(self):
        self.assertIsNone(self.accept(self.sample(uptime_ms=1000)))
        self.assertEqual(self.odom.last_reason, "invalid_mcu_interval")

    def test_unphysical_jump_does_not_teleport(self):
        self.assertIsNone(self.accept(self.sample(left=1000, right=1000)))
        self.assertEqual(self.odom.last_reason, "implausible_counts")
        self.assertEqual(self.odom.x, 0)
        result = self.accept(self.sample(sequence=2, left=1010, right=1010))
        self.assertAlmostEqual(result.x, 0.01)

    def test_counter_reset_is_not_unsigned_reverse_distance(self):
        self.accept(self.sample())
        self.assertIsNone(self.accept(self.sample(sequence=2, left=0, right=0)))
        self.assertEqual(self.odom.last_reason, "implausible_counts")
        result = self.accept(self.sample(sequence=3, left=10, right=10))
        self.assertAlmostEqual(result.x, 0.02)

    def test_invalid_direction_discards_unknown_motion(self):
        self.assertIsNone(self.accept(self.sample(direction_valid=False)))
        self.assertIsNone(self.accept(self.sample(sequence=2, left=20, right=20)))
        result = self.accept(self.sample(sequence=3, left=30, right=30))
        self.assertAlmostEqual(result.x, 0.01)

    def test_external_invalidation_does_not_integrate_across_wrong_frames(self):
        self.accept(self.sample())
        self.odom.invalidate("wrong_feedback_frame")
        self.assertEqual(self.odom.last_reason, "wrong_feedback_frame")
        self.assertAlmostEqual(self.odom.x, 0.01)
        self.assertIsNone(self.accept(self.sample(sequence=2, left=30, right=30)))
        result = self.accept(self.sample(sequence=3, left=40, right=40))
        self.assertAlmostEqual(result.x, 0.02)

    def test_wiring_and_fg_fault_flags_block_odometry(self):
        for flag in (1, 4, 5):
            with self.subTest(flag=flag):
                odom = WheelOdometry(self.calibration)
                odom.accept(self.first, self.first.stamp_ns)
                sample = self.sample(status=flag)
                self.assertIsNone(odom.accept(sample, sample.stamp_ns))
                self.assertEqual(odom.x, 0)

    def test_watchdog_can_still_report_measured_coasting(self):
        result = self.accept(self.sample(status=2))
        self.assertAlmostEqual(result.x, 0.01)

    def test_invalid_inputs_are_rejected(self):
        for kwargs in (
            {"left_ticks": -1}, {"right_ticks": UINT32_MASK + 1},
            {"sequence": True}, {"session": 0}, {"stamp_ns": 0},
            {"direction_valid": 1}, {"status": -1},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.accept(self.sample(**kwargs)))

    def test_invalid_calibration_prevents_startup(self):
        for field in self.calibration.__dataclass_fields__:
            for value in (0, -1, math.inf, math.nan):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        replace(self.calibration, **{field: value})

    def test_extreme_calibration_cannot_produce_infinite_pose(self):
        for kwargs in (
            {"left_wheel_radius_m": 1e308},
            {"wheel_separation_m": 1e-320},
            {"left_pulses_per_revolution": 1e-320},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    replace(self.calibration, **kwargs)


if __name__ == "__main__":
    unittest.main()
