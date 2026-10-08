import os
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    pkg_autonav = get_package_share_directory('robot_autonav')
    pkg_nav2_bringup = get_package_share_directory('nav2_bringup')

    # Ruta predeterminada del mapa y parámetros
    map_yaml_file = LaunchConfiguration('map', default=os.path.join(pkg_autonav, 'maps', 'mi_mapa.yaml'))
    params_file = LaunchConfiguration('params_file', default=os.path.join(pkg_autonav, 'config', 'nav2_params.yaml'))

    return LaunchDescription([
        # 1. TF Estática del Lidar (0.15m arriba)
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_link_to_laser',
            arguments=['0', '0', '0.15', '0', '0', '0', 'base_link', 'laser']
        ),

        # 2. Stack de Nav2
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(
                os.path.join(pkg_nav2_bringup, 'launch', 'bringup_launch.py')
            ),
            launch_arguments={
                'map': map_yaml_file,
                'params_file': params_file,
                'use_sim_time': 'false',
                'autostart': 'true'
            }.items()
        )
    ])
