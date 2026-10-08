"""Diagnóstico local, sin puertos abiertos ni impresión de credenciales."""
import importlib.util
import json
import os
import platform
import shutil
from pathlib import Path
root=Path(__file__).resolve().parents[1]
result={"os":platform.platform(),"architecture":platform.machine(),"python":platform.python_version(),
        "ros_distribution":os.getenv("ROS_DISTRO","not_loaded"),
        "ros_domain":os.getenv("ROS_DOMAIN_ID","default"),
        "colcon":bool(shutil.which("colcon")),"ros2_cli":bool(shutil.which("ros2")),
        "serial_devices":[str(p) for p in Path('/dev/serial/by-id').glob('*')],
        "modules":{m:bool(importlib.util.find_spec(m)) for m in ('aiohttp','rclpy','serial','yaml')},
        "overlay":(root/'robot/ros2/install/setup.bash').exists()}
print(json.dumps(result,indent=2))
