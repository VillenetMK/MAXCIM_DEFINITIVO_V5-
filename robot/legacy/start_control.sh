#!/usr/bin/env bash
set -eo pipefail
MAX_CONTROL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
MAX_CONTROL_PYTHON="$HOME/.local/share/max-control/venv/bin/python"
if [[ ! -x "$MAX_CONTROL_PYTHON" ]]; then
  echo 'Instala las dependencias indicadas en docs/CONTROL_WEB.md' >&2
  exit 1
fi
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}"
MAX_CONTROL_ARGS=(--host 127.0.0.1)
if [[ "${1:-}" == '--lan' ]]; then
  shift
  while IFS= read -r address; do MAX_CONTROL_ARGS+=(--host "$address"); done < <(python3 - <<'PY'
import ipaddress,json,subprocess
networks=[ipaddress.ip_network(n) for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']]
data=json.loads(subprocess.check_output(['ip','-j','-4','addr','show','up']))
for interface in data:
    if interface['ifname'] not in ('eth0','wlan0'): continue
    for info in interface.get('addr_info',[]):
        address=info['local']
        if any(ipaddress.ip_address(address) in network for network in networks): print(address)
PY
  )
fi
exec "$MAX_CONTROL_PYTHON" "$MAX_CONTROL_ROOT/control_web/server.py" "${MAX_CONTROL_ARGS[@]}" "$@"
