"""Start only the LiDAR; select its port without probing motor controllers."""
import os
from pathlib import Path

import serial.tools.list_ports
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def resolve_port(requested):
    reserved = {
        os.path.realpath(path) for path in ("/dev/arduino", "/dev/esp32")
        if os.path.exists(path)
    }
    if requested != "auto":
        if not os.path.exists(requested):
            raise RuntimeError("Puerto LiDAR inexistente: " + requested)
        if os.path.realpath(requested) in reserved:
            raise RuntimeError("El puerto indicado pertenece a un controlador de motores.")
        return requested
    for alias in ("/dev/rplidar", "/dev/lidar"):
        if os.path.exists(alias) and os.path.realpath(alias) not in reserved:
            return alias
    candidates = sorted({
        os.path.realpath(port.device)
        for port in serial.tools.list_ports.comports()
        if Path(port.device).name.startswith(("ttyUSB", "ttyACM"))
        and os.path.realpath(port.device) not in reserved
    })
    if len(candidates) != 1:
        raise RuntimeError(
            "No se puede identificar el LiDAR sin ambigüedad. "
            "Indica lidar_port:=/dev/PUERTO. Candidatos: " + repr(candidates)
        )
    return candidates[0]


def start_lidar(context):
    port = resolve_port(LaunchConfiguration("lidar_port").perform(context))
    return [Node(
        package="rplidar_ros", executable="rplidar_node", name="rplidar_node",
        output="screen",
        parameters=[{
            "serial_port": port,
            "serial_baudrate": ParameterValue(
                LaunchConfiguration("lidar_baudrate"), value_type=int),
            "frame_id": "laser",
            "angle_compensate": True,
            "scan_mode": "",
            "scan_frequency": ParameterValue(
                LaunchConfiguration("scan_frequency"), value_type=float),
            "auto_standby": False,
            "use_sim_time": False,
        }],
    )]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("lidar_port", default_value="auto"),
        DeclareLaunchArgument("lidar_baudrate", default_value="115200"),
        DeclareLaunchArgument("scan_frequency", default_value="5.0"),
        OpaqueFunction(function=start_lidar),
    ])
