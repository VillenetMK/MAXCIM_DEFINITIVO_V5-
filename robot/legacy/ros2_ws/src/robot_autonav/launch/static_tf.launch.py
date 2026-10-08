from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        # Publica la transformación fija entre base_link y laser_frame
        # Argumentos: x y z roll pitch yaw frame_id child_frame_id
        Node(
            package='tf2_ros',
            executable='static_transform_publisher',
            name='base_link_to_laser',
            arguments=['0', '0', '0.15', '0', '0', '0', 'base_link', 'laser']
        )
    ])
