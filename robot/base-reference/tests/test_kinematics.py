"""Casos físicos conocidos para una base diferencial sin inversión."""

import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ros2" / "src" / "maxcim_base"))
from maxcim_base.kinematics import Geometry, wheel_targets  # noqa: E402


class KinematicsTests(unittest.TestCase):
    def setUp(self):
        self.geometry = Geometry(0.1, 0.1, 0.4, 120, 120, 0.5)

    def test_stopped_and_straight_motion(self):
        self.assertEqual(wheel_targets(0, 0, self.geometry), (0, 0))
        left, right = wheel_targets(0.2, 0, self.geometry)
        self.assertAlmostEqual(left, 120 / math.pi)
        self.assertEqual(left, right)

    def test_left_and_right_arcs(self):
        left, right = wheel_targets(0.2, 0.5, self.geometry)
        self.assertAlmostEqual(right / left, 3)
        mirror_left, mirror_right = wheel_targets(0.2, -0.5, self.geometry)
        self.assertAlmostEqual(left, mirror_right)
        self.assertAlmostEqual(right, mirror_left)

    def test_turn_around_one_stationary_wheel(self):
        left, right = wheel_targets(0.1, 0.5, self.geometry)
        self.assertEqual(left, 0)
        self.assertAlmostEqual(right, 120 / math.pi)

    def test_reverse_in_place_and_too_tight_turn_rejected(self):
        for linear, angular in ((-0.1, 0), (0, 0.5), (0, -0.5), (0.1, 1), (0.1, -1)):
            with self.subTest(linear=linear, angular=angular), self.assertRaises(ValueError):
                wheel_targets(linear, angular, self.geometry)

    def test_saturation_preserves_curvature(self):
        left, right = wheel_targets(1, 2.5, self.geometry)
        self.assertAlmostEqual(right / left, 3)
        self.assertAlmostEqual(right * (2 * math.pi * 0.1) / 120, 0.5)

    def test_wheel_radius_and_pulse_calibration_are_independent(self):
        geometry = Geometry(0.1, 0.2, 0.4, 120, 360, 0.5)
        left, right = wheel_targets(0.2, 0, geometry)
        self.assertAlmostEqual(right / left, 1.5)

    def test_nonfinite_commands_are_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                wheel_targets(value, 0, self.geometry)
            with self.subTest(angular=value), self.assertRaises(ValueError):
                wheel_targets(0, value, self.geometry)

    def test_every_geometry_field_must_be_finite_positive(self):
        valid = [0.1, 0.1, 0.4, 120, 120, 0.5]
        for index in range(len(valid)):
            for invalid in (0, -1, float("nan"), float("inf"), True):
                values = valid[:]
                values[index] = invalid
                with self.subTest(index=index, invalid=invalid), self.assertRaises(ValueError):
                    Geometry(*values)

    def test_roundoff_near_zero_does_not_request_reverse(self):
        left, right = wheel_targets(0.3, 1.5, self.geometry)
        self.assertEqual(left, 0)
        self.assertGreater(right, 0)


if __name__ == "__main__":
    unittest.main()
