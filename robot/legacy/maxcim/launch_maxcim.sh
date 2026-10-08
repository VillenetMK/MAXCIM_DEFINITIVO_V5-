#!/bin/bash
# Lanza todos los nodos de MAXCIM con su entorno completo.

WS=/home/maxcim/mciav2_ws
LOGS=/tmp/maxcim_logs
mkdir -p "$LOGS"

# Fuente única de verdad para el entorno ROS
ENV_SETUP="set -a && source $WS/.env && set +a && source /opt/ros/jazzy/setup.bash && source $WS/install/setup.bash"

# ── matar instancias previas ───────────────────────────────────────────────────
echo "[*] Deteniendo nodos previos..."
pkill -f "camera_node|face_recognition_node|memory_node|mic_node|gemini_live_node" 2>/dev/null
sleep 1

# ── lanzar nodos (cada uno en su propio bash con entorno completo) ─────────────
launch_node() {
    local name="$1"; shift
    local log="$LOGS/${name}.log"
    > "$log"
    bash -c "$ENV_SETUP && exec $*" > "$log" 2>&1 &
    echo "[+] $name PID=$!"
}

launch_node camera_node          "ros2 run vision_pkg camera_node --ros-args -p camera_index:=0 -p publish_rate:=10.0"
launch_node face_recognition_node "ros2 run vision_pkg face_recognition_node"
launch_node memory_node           "ros2 run memory_pkg memory_node"
launch_node mic_node              "ros2 run audio_pkg mic_node"

echo "[*] Esperando 6 s para que inicien los nodos base..."
sleep 6

echo "[*] Estado inicial de logs:"
for n in camera_node face_recognition_node memory_node mic_node; do
    echo "── $n ──"
    tail -5 "$LOGS/${n}.log" 2>/dev/null || echo "  (sin salida)"
done

echo ""
echo "[*] Lanzando gemini_live_node..."
launch_node gemini_live_node "ros2 run reasoning_pkg gemini_live_node"

echo ""
echo "Sistema MAXCIM lanzado. PIDs activos:"
pgrep -la -f "camera_node|face_recognition_node|memory_node|mic_node|gemini_live_node" 2>/dev/null

echo ""
echo "Logs en: $LOGS/"
echo "Para monitorear: tail -f $LOGS/*.log"
echo "Para detener:    pkill -f 'camera_node|face_recognition|memory_node|mic_node|gemini_live'"
