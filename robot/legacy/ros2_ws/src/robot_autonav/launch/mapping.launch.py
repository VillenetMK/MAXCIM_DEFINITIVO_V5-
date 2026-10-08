"""Asynchronous LiDAR + encoder mapping, without IMU or Nav2 controllers."""
import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    own = get_package_share_directory("robot_autonav")
    base = get_package_share_directory("robot_base")
    slam = get_package_share_directory("slam_toolbox")
    return LaunchDescription([
        DeclareLaunchArgument("rviz", default_value="false"),
        DeclareLaunchArgument(
            "bringup_robot", default_value="false",
            description="Start base and LiDAR too; false uses an existing /scan and odometry."),
        DeclareLaunchArgument("lidar_port", default_value="auto"),
        DeclareLaunchArgument("arduino_port", default_value="/dev/arduino"),
        DeclareLaunchArgument(
            "slam_params_file",
            default_value=os.path.join(own, "config", "slam_lidar.yaml")),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(base, "launch", "robotall.launch.py")),
            condition=IfCondition(LaunchConfiguration("bringup_robot")),
            launch_arguments={
                "lidar_port": LaunchConfiguration("lidar_port"),
                "arduino_port": LaunchConfiguration("arduino_port"),
            }.items(),
        ),
        Node(
            package="rviz2", executable="rviz2", name="rviz2",
            arguments=["-d", os.path.join(own, "rviz", "mapping.rviz")],
            parameters=[{"use_sim_time": False}],
            condition=IfCondition(LaunchConfiguration("rviz")),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(slam, "launch", "online_async_launch.py")),
            launch_arguments={
                "use_sim_time": "false",
                "autostart": "true",
                "use_lifecycle_manager": "false",
                "slam_params_file": LaunchConfiguration("slam_params_file"),
            }.items(),
        ),
    ])
