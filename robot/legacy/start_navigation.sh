#!/usr/bin/env bash
set -eo pipefail
MAX_NAV_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID=0
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
exec ros2 launch "$MAX_NAV_ROOT/ops/studio_navigation.launch.py"
