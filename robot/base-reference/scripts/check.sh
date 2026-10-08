#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export PYTHONPATH="$project_root/src/maxcim_base:$project_root/src/maxcim_odometry${PYTHONPATH:+:$PYTHONPATH}"
python3 -m unittest discover -s tests -v
bash tests/firmware/run.sh
python3 -m compileall -q src scripts tests
