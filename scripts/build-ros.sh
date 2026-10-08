#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
case "${1:-simulation}" in
 simulation) packages=(maxcim_interfaces maxcim_control) ;;
 raspberry) packages=(maxcim_interfaces maxcim_control maxcim_base maxcim_odometry rplidar_ros) ;;
 jetson) packages=(maxcim_interfaces maxcim_control robot_interfaces audio_pkg orbbec_vision_pkg memory_pkg reasoning_pkg) ;;
 *) echo 'Uso: bash scripts/build-ros.sh simulation|raspberry|jetson' >&2;exit 2 ;;
esac
if ! command -v colcon >/dev/null || [[ -z "${ROS_DISTRO:-}" ]];then
 echo 'Carga primero el entorno ROS compatible con el sistema de esta máquina.' >&2;exit 1
fi
colcon --log-base robot/ros2/log build --base-paths robot/ros2/src \
 --build-base robot/ros2/build --install-base robot/ros2/install \
 --packages-select "${packages[@]}" --symlink-install
