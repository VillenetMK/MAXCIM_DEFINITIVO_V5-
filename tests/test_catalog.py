import unittest
from pathlib import Path
from maxcim_core.catalog import Catalog, calibrated_moves

ROOT=Path(__file__).resolve().parents[1]
class CatalogTests(unittest.TestCase):
    def setUp(self):self.catalog=Catalog(ROOT/"robot/legacy/BRAZOS_MAXICM_v8/data")
    def test_real_gesture_names_expand(self):
        for name in self.catalog.voice:
            groups=self.catalog.gesture(name)
            self.assertTrue(groups,name)
    def test_calibration_blocks_before_any_motion(self):
        with self.assertRaises(ValueError):calibrated_moves(self.catalog.gesture("saludar"),{"verified":False})
    def test_circular_reference_is_rejected(self):
        self.catalog.actions["bad"]={"sequence":["bad"]}
        with self.assertRaises(ValueError):self.catalog.expand("bad")
    def test_unknown_gesture_is_rejected(self):
        with self.assertRaises(ValueError):self.catalog.gesture("raw_serial")
    def test_parallel_same_servo_rejected(self):
        self.catalog.actions["bad"]={"sequence":[["B2-SM1","B2-SM1"]]}
        with self.assertRaises(ValueError):self.catalog.expand("bad")
    def test_out_of_range_not_silently_clamped(self):
        calibration={"verified":True,"servos":{"4":{"logical_min":125,"logical_max":200,"pulse_min":100,"pulse_max":500}}}
        with self.assertRaises(ValueError):calibrated_moves([[{"channel":4,"angle":270}]],calibration)
