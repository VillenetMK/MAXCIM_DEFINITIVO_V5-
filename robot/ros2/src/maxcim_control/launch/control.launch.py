from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("physical", default_value="false"),
        Node(package="maxcim_control", executable="gateway", output="screen",
             parameters=[{"physical": ParameterValue(LaunchConfiguration("physical"), value_type=bool)}]),
    ])
