"""Standalone measured wheel odometry; requires a calibrated parameter file."""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    default_parameters = os.path.join(
        get_package_share_directory("maxcim_odometry"), "config", "hardware.yaml"
    )
    return LaunchDescription([
        DeclareLaunchArgument("params_file", default_value=default_parameters),
        Node(
            package="maxcim_odometry", executable="wheel_odometry",
            name="wheel_odometry", output="screen",
            parameters=[LaunchConfiguration("params_file")],
        ),
    ])
