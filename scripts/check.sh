#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
export PYTHONPATH="$task_root/packages:$task_root/backend:$task_root/robot/ros2/src/maxcim_base:$task_root/robot/ros2/src/maxcim_odometry${PYTHONPATH:+:$PYTHONPATH}"
python -m unittest discover -s tests -v
python -m unittest discover -s robot/base-reference/tests -p 'test_*.py' -v
bash robot/base-reference/tests/firmware/run.sh
bash tests/firmware/run.sh
python -m compileall -q backend packages robot/ros2/src apps/education
node --check frontend/assets/app.js
