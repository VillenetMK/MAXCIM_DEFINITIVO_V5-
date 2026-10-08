"""Servicios físicos opcionales; gateway enclavado y puertos explícitos."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    declarations=[DeclareLaunchArgument("base_config",default_value=""),
        DeclareLaunchArgument("odometry_config",default_value=""),
        DeclareLaunchArgument("base",default_value="false"),
        DeclareLaunchArgument("lidar",default_value="false"),
        DeclareLaunchArgument("lidar_port",default_value=""),
        DeclareLaunchArgument("arms",default_value="false"),
        DeclareLaunchArgument("arms_port",default_value=""),
        DeclareLaunchArgument("arms_calibration",default_value=""),
        DeclareLaunchArgument("arms_catalog",default_value="")]
    nodes=[Node(package="maxcim_control",executable="gateway",parameters=[{"physical":True}],output="screen"),
        Node(package="maxcim_base",executable="nano_base",parameters=[LaunchConfiguration("base_config")],
             remappings=[("/cmd_vel","/maxcim/base/cmd_vel")],output="screen",condition=IfCondition(LaunchConfiguration("base"))),
        Node(package="maxcim_odometry",executable="wheel_odometry",parameters=[LaunchConfiguration("odometry_config")],
             output="screen",condition=IfCondition(LaunchConfiguration("base"))),
        Node(package="rplidar_ros",executable="rplidar_composition",parameters=[{"serial_port":LaunchConfiguration("lidar_port"),"serial_baudrate":115200,"frame_id":"laser","inverted":False,"angle_compensate":True}],
             output="screen",condition=IfCondition(LaunchConfiguration("lidar"))),
        Node(package="maxcim_control",executable="arms",parameters=[{"serial_port":LaunchConfiguration("arms_port"),"calibration_file":LaunchConfiguration("arms_calibration"),"catalog_dir":LaunchConfiguration("arms_catalog")}],
             output="screen",condition=IfCondition(LaunchConfiguration("arms")))]
    return LaunchDescription(declarations+nodes)
