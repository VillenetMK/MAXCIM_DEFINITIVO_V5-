#!/usr/bin/env bash
set -eo pipefail
MAX_STUDIO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source /opt/ros/jazzy/setup.bash
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-0}"
export ROS_AUTOMATIC_DISCOVERY_RANGE="${ROS_AUTOMATIC_DISCOVERY_RANGE:-LOCALHOST}"
MAX_STUDIO_ARGS=(--host 127.0.0.1)
if [[ "${1:-}" == '--lan' ]]; then
  shift
  while IFS= read -r address; do MAX_STUDIO_ARGS+=(--host "$address"); done < <(python3 - <<'PY'
import ipaddress,json,subprocess
networks=[ipaddress.ip_network(n) for n in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']]
for interface in json.loads(subprocess.check_output(['ip','-j','-4','addr','show','up'])):
    if interface['ifname'] not in ('eth0','wlan0'): continue
    for info in interface.get('addr_info',[]):
        if any(ipaddress.ip_address(info['local']) in n for n in networks): print(info['local'])
PY
  )
fi
exec "$HOME/.local/share/max-control/venv/bin/python" "$MAX_STUDIO_ROOT/studio/server.py" "${MAX_STUDIO_ARGS[@]}" "$@"
