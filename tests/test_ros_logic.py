"""Ejecutar callbacks reales con dobles. No sustituye la prueba con DDS/hardware."""
import ast
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
ROOT=Path(__file__).resolve().parents[1]

def load_class(file,name,namespace):
    path=ROOT/"robot/ros2/src/maxcim_control/maxcim_control"/file
    tree=ast.parse(path.read_text())
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==name)
    env={"Node":object,"json":json,"time":time,**namespace}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),"exec"),env)
    return env[name]

class RosLogicTests(unittest.TestCase):
    def setUp(self):
        self.Arms=load_class("arms.py","Arms",{})
        self.a=self.Arms.__new__(self.Arms)
        self.a.identified=True;self.a.pending_enable=False;self.a.enabled=True
        self.a.awaiting=True;self.a.group_id=3;self.a.wait_after=.5
        self.a.next_group=0;self.a.positions={}
    def test_old_state_does_not_confirm_new_group(self):
        self.a.receive("STATE 1 0 2 200 200 200 200 200 200")
        self.assertTrue(self.a.awaiting)
        self.assertEqual(self.a.wait_after,.5)
    def test_matching_group_only_completes_when_stopped(self):
        self.a.receive("STATE 1 1 3 200 200 200 200 200 200")
        self.assertFalse(self.a.awaiting);self.assertEqual(self.a.wait_after,.5)
        self.a.receive("STATE 1 0 3 250 250 250 250 250 250")
        self.assertEqual(self.a.wait_after,0);self.assertGreater(self.a.next_group,time.monotonic())
    def test_old_disabled_state_cannot_ack_enable(self):
        self.a.enabled=False;self.a.pending_enable=True
        self.a.receive("STATE 0 0 0 200 200 200 200 200 200")
        self.assertTrue(self.a.pending_enable);self.assertFalse(self.a.enabled)
        self.a.receive("STATE 1 0 0 200 200 200 200 200 200")
        self.assertFalse(self.a.pending_enable);self.assertTrue(self.a.enabled)
    def test_invalid_state_rejected(self):
        for msg in ("STATE 1 0", "STATE 1 0 3 900 200 200 200 200 200"):
            with self.assertRaises(ValueError):self.a.receive(msg)
    def test_scan_infinity_is_clear_but_invalid_ray_fraction_blocks(self):
        import math
        Gateway=load_class("gateway.py","Gateway",{"math":math})
        g=Gateway.__new__(Gateway);g.controller=SimpleNamespace(scan=Mock(),scan_at=0)
        g.scan(SimpleNamespace(range_min=.1,range_max=8,ranges=[float("inf")]*10))
        g.controller.scan.assert_called_once_with(8)
        g.scan(SimpleNamespace(range_min=.1,range_max=8,ranges=[float("nan")]*10))
        self.assertEqual(g.controller.scan_at,float("-inf"))
    def test_voice_cannot_acquire_or_bypass_operator_permission(self):
        Gateway=load_class("gateway.py","Gateway",{})
        g=Gateway.__new__(Gateway)
        g.controller=SimpleNamespace(tick=Mock(),owner="operator",voice_enabled=False)
        g.state=lambda:{"mode":"ros2-simulation"}
        g.execute=Mock()
        response=SimpleNamespace()
        req=SimpleNamespace(command="arm",owner="model")
        g.voice_action(req,response);self.assertFalse(response.accepted);g.execute.assert_not_called()
        g.controller.voice_enabled=True;req.command="acquire"
        g.voice_action(req,response);self.assertFalse(response.accepted);g.execute.assert_not_called()
        req.command="arm";g.voice_action(req,response)
        self.assertEqual(req.owner,"operator");g.execute.assert_called_once()
