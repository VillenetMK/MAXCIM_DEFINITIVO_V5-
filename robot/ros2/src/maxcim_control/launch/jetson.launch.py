"""Percepción opcional; ninguna de estas tareas publica a la base física."""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

def generate_launch_description():
    flags={"camera":"true","audio":"true","faces":"false","memory":"false","conversation":"false"}
    actions=[DeclareLaunchArgument(k,default_value=v) for k,v in flags.items()]
    definitions=[("camera","orbbec_vision_pkg","orbbec_camera_node",{"width":640,"height":480,"publish_rate":10.0}),
                 ("audio","audio_pkg","mic_node",{}),
                 ("faces","orbbec_vision_pkg","orbbec_face_recognition_node",{"use_gpu":False}),
                 ("memory","memory_pkg","memory_node",{}),
                 ("conversation","reasoning_pkg","gemini_live_node",{})]
    for flag,package,executable,params in definitions:
        actions.append(Node(package=package,executable=executable,parameters=[params],output="screen",condition=IfCondition(LaunchConfiguration(flag))))
    return LaunchDescription(actions)
