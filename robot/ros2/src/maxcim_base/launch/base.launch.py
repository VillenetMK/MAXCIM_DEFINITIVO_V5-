from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from pathlib import Path


def generate_launch_description():
    default = str(Path(get_package_share_directory('maxcim_base')) / 'config/hardware.yaml')
    config = LaunchConfiguration('config')
    return LaunchDescription([
        DeclareLaunchArgument('config', default_value=default),
        DeclareLaunchArgument('odometry', default_value='false',
                             description='true solo después de medir/calibrar'),
        Node(package='maxcim_base', executable='nano_base', name='nano_base',
             output='screen', parameters=[config]),
        Node(package='maxcim_odometry', executable='wheel_odometry',
             name='wheel_odometry', output='screen', parameters=[config],
             condition=IfCondition(LaunchConfiguration('odometry'))),
    ])
