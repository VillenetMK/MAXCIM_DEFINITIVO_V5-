#!/usr/bin/env bash
set -eo pipefail
MAX_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$MAX_PROJECT_ROOT/ros2_ws/install/setup.bash" ]]; then
  echo "Primero compila: cd $MAX_PROJECT_ROOT/ros2_ws && source /opt/ros/jazzy/setup.bash && colcon build --packages-select rplidar_ros robot_base robot_autonav" >&2
  exit 1
fi
source /opt/ros/jazzy/setup.bash
source "$MAX_PROJECT_ROOT/ros2_ws/install/setup.bash"
exec ros2 launch robot_autonav mapping.launch.py bringup_robot:=true rviz:=true "$@"
