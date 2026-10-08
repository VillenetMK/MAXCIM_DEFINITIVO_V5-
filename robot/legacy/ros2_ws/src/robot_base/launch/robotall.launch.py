"""Base, continuous LiDAR and robot TF; no IMU and no artificial start delay."""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    share = get_package_share_directory("robot_base")
    with open(os.path.join(share, "urdf", "robot.urdf"), encoding="utf-8") as source:
        description = source.read()
    return LaunchDescription([
        DeclareLaunchArgument("lidar_port", default_value="auto"),
        DeclareLaunchArgument("lidar_baudrate", default_value="115200"),
        DeclareLaunchArgument("scan_frequency", default_value="5.0"),
        DeclareLaunchArgument("arduino_port", default_value="/dev/arduino"),
        Node(
            package="robot_state_publisher", executable="robot_state_publisher",
            name="robot_state_publisher", output="screen",
            parameters=[{"robot_description": description, "use_sim_time": False}],
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, "launch", "lidar.launch.py")),
            launch_arguments={
                "lidar_port": LaunchConfiguration("lidar_port"),
                "lidar_baudrate": LaunchConfiguration("lidar_baudrate"),
                "scan_frequency": LaunchConfiguration("scan_frequency"),
            }.items(),
        ),
        Node(
            package="robot_base", executable="base_node", name="robot_base_node",
            output="screen",
            parameters=[{
                "port": LaunchConfiguration("arduino_port"),
                "baudrate": 115200,
                "wheel_separation": 0.65,
                "wheel_radius": 0.10,
                "base_frame": "base_footprint",
                "use_sim_time": False,
            }],
        ),
    ])
