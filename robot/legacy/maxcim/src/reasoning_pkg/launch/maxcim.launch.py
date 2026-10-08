from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    use_depth_overlay = LaunchConfiguration('use_depth_overlay', default='false')

    return LaunchDescription([
        DeclareLaunchArgument('use_depth_overlay', default_value='false'),

        Node(
            package='orbbec_vision_pkg',
            executable='orbbec_camera_node',
            name='orbbec_camera_node',
            parameters=[{'width': 640, 'height': 480}],
            output='screen',
        ),
        Node(
            package='orbbec_vision_pkg',
            executable='orbbec_face_recognition_node',
            name='orbbec_face_recognition_node',
            output='screen',
        ),
        Node(
            package='orbbec_vision_pkg',
            executable='orbbec_proximity_node',
            name='orbbec_proximity_node',
            output='screen',
        ),
        Node(
            package='memory_pkg',
            executable='memory_node',
            name='memory_node',
            output='screen',
        ),
        Node(
            package='audio_pkg',
            executable='mic_node',
            name='mic_node',
            output='screen',
        ),
        Node(
            package='reasoning_pkg',
            executable='gemini_live_node',
            name='gemini_live_node',
            parameters=[{'use_depth_overlay': use_depth_overlay}],
            output='screen',
        ),
    ])
