#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_binary="$(mktemp "${TMPDIR:-/tmp}/maxcim-firmware-test.XXXXXX")"
trap 'rm -f "$test_binary"' EXIT
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -pedantic \
  -I"$task_root/firmware/maxcim_base_nano" \
  "$task_root/firmware/maxcim_base_nano/control_core.cpp" \
  "$task_root/tests/firmware/test_control.cpp" -o "$test_binary"
"$test_binary"
