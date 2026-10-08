#!/usr/bin/env python3
"""Jetson camera/mic only: local DDS, no Gemini, no memories, no media files.

Output stays in tmux's RAM buffer. ROS file logging is disabled. Not enabled at boot.
"""
import os
from pathlib import Path
import shlex
import subprocess


def main():
    active=set()
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            active.update(Path(arg.decode(errors='replace')).name
                          for arg in (proc/'cmdline').read_bytes().split(b'\0')[:2] if arg)
        except OSError:
            pass
    if active & {'gemini_live_node','memory_node'}:
        raise SystemExit('Hay IA o memoria activa; no se inicia el modo local aislado.')
    if active & {'orbbec_camera_node','mic_node'}:
        raise SystemExit('Los sensores ya tienen un proceso; se conserva sin abrirlos de nuevo.')
    if subprocess.run(['tmux','has-session','-t','max-local-sensors'],capture_output=True).returncode==0:
        raise SystemExit('La sesión max-local-sensors ya existe.')
    workspace=Path.home()/'mciav2_ws'
    runtime=Path('/run/user')/str(os.getuid())/'max-local-sensors'
    runtime.mkdir(exist_ok=True,mode=0o700)
    setup='source /opt/ros/jazzy/setup.bash && source '+shlex.quote(str(workspace/'install/setup.bash'))
    environment=('export ROS_DOMAIN_ID=0 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ROS_LOG_DIR='+shlex.quote(str(runtime))
        +' XDG_RUNTIME_DIR='+shlex.quote(str(runtime.parent))
        +' DBUS_SESSION_BUS_ADDRESS='+shlex.quote('unix:path='+str(runtime.parent/'bus')))
    flags=' --ros-args --disable-rosout-logs --disable-external-lib-logs --disable-stdout-logs'
    camera=setup+' && '+environment+' && ros2 run orbbec_vision_pkg orbbec_camera_node'+flags+' -p width:=640 -p height:=480'
    mic=setup+' && '+environment+' && ros2 run audio_pkg mic_node'+flags
    subprocess.run(['tmux','new-session','-d','-s','max-local-sensors','-n','camera','bash','-c',camera],check=True)
    subprocess.run(['tmux','new-window','-t','max-local-sensors','-n','microphone','bash','-c',mic],check=True)
    print('Sensores locales iniciados. Gemini y memoria permanecen apagados.')
    print('Detener: tmux kill-session -t max-local-sensors')


if __name__=='__main__':
    main()
