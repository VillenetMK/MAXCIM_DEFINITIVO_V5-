#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
test_binary="$(mktemp "${TMPDIR:-/tmp}/maxcim-arms-test.XXXXXX")"
trap 'rm -f "$test_binary"' EXIT
"${CXX:-g++}" -std=c++11 -Wall -Wextra -Werror -pedantic -I"$task_root/tests/firmware" "$task_root/tests/firmware/test_arms.cpp" -o "$test_binary"
"$test_binary"
