"""Isolated Nav2 output: only Studio can forward /max/nav_cmd_vel to the base."""
from pathlib import Path
import json
import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    profile=json.loads((Path.home()/'.config/max-studio/navigation.json').read_text())
    radius=profile.get('robot_radius')
    if profile.get('verified') is not True or type(radius) not in (int,float) or not .3<=radius<=1:
        raise RuntimeError('Primero verifica las dimensiones de la base en navigation.json')
    params=yaml.safe_load((Path(get_package_share_directory('nav2_bringup'))/'params/nav2_params.yaml').read_text())
    controller=params['controller_server']['ros__parameters']
    controller.update(enable_stamped_cmd_vel=False)
    # Keep the installed distribution's plugins and critics; only constrain motion.
    follower=controller['FollowPath']
    follower.update(max_vel_x=.10,min_vel_x=0.,max_vel_y=0.,min_vel_y=0.,
                    max_speed_xy=.10,min_speed_xy=0.,max_vel_theta=.30,min_speed_theta=0.,
                    acc_lim_x=.15,acc_lim_y=0.,acc_lim_theta=.4,decel_lim_x=-.15,
                    decel_lim_y=0.,decel_lim_theta=-.4)
    params['velocity_smoother']['ros__parameters'].update(
        enable_stamped_cmd_vel=False,max_velocity=[.10,0.,.30],min_velocity=[-.08,0.,-.30],
        max_accel=[.15,0.,.4],max_decel=[-.15,0.,-.4],velocity_timeout=.25)
    params['collision_monitor']['ros__parameters'].update(
        enable_stamped_cmd_vel=False,cmd_vel_in_topic='/cmd_vel_smoothed',
        cmd_vel_out_topic='/max/nav_cmd_vel',source_timeout=.5)
    params['behavior_server']['ros__parameters'].update(enable_stamped_cmd_vel=False,
        max_rotational_vel=.30,min_rotational_vel=.07,rotational_acc_lim=.4)
    for name in ('local_costmap','global_costmap'):
        cost=params[name][name]['ros__parameters']
        cost.pop('footprint',None)
        cost.update(robot_radius=float(radius),robot_base_frame='base_link')
        cost.setdefault('inflation_layer',{}).update(inflation_radius=float(radius)+.25)
        for layer in ('obstacle_layer','voxel_layer'):
            if layer in cost and 'scan' in cost[layer]:cost[layer]['scan']['topic']='/scan'
    params['bt_navigator']['ros__parameters'].update(robot_base_frame='base_link',global_frame='map')
    definitions=[('nav2_controller','controller_server'),('nav2_planner','planner_server'),
                 ('nav2_smoother','smoother_server'),('nav2_behaviors','behavior_server'),
                 ('nav2_bt_navigator','bt_navigator'),('nav2_velocity_smoother','velocity_smoother'),
                 ('nav2_collision_monitor','collision_monitor')]
    # A transient parameter file, never a bag or a media recording.
    import os,tempfile
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR','/tmp'))
    with tempfile.NamedTemporaryFile(mode='w',prefix='max-nav-',suffix='.yaml',dir=runtime,delete=False) as f:
        yaml.safe_dump(params,f); filename=f.name
    actions=[]
    for package,name in definitions:
        remaps=[('/cmd_vel','/max/nav_cmd_vel')]
        if name in ('controller_server','behavior_server','velocity_smoother'):
            remaps=[('/cmd_vel','/cmd_vel_nav')]
        actions.append(Node(package=package,executable=name,name=name,parameters=[filename],remappings=remaps,output='screen'))
    actions.append(Node(package='nav2_lifecycle_manager',executable='lifecycle_manager',name='max_navigation_lifecycle',
        parameters=[{'autostart':True,'node_names':[n for _,n in definitions]}]))
    return LaunchDescription(actions)
