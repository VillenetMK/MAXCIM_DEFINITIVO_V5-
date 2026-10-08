#!/usr/bin/env bash
set -eo pipefail
# ROS setup scripts read optional variables that may not be defined yet.
source /opt/ros/jazzy/setup.bash
set -u
export ROS_DOMAIN_ID=0 ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
exec /usr/bin/python3 "$HOME/.local/share/max-studio-jetson/mobility_voice.py" --listen
