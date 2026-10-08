#!/bin/bash
SESSION="maxcim"
WS="/home/maxcim/mciav2_ws"
ENV_SETUP="set -a && source $WS/.env && set +a && source /opt/ros/jazzy/setup.bash && source $WS/install/setup.bash"

tmux kill-session -t $SESSION 2>/dev/null || true
tmux new-session -d -s $SESSION -x 220 -y 50

tmux rename-window -t $SESSION:0 "camera"
tmux send-keys -t $SESSION:0 "$ENV_SETUP && ros2 run orbbec_vision_pkg orbbec_camera_node --ros-args -p width:=640 -p height:=480" Enter

tmux new-window -t $SESSION -n "faces"
tmux send-keys -t $SESSION:1 "$ENV_SETUP && ros2 run orbbec_vision_pkg orbbec_face_recognition_node" Enter

tmux new-window -t $SESSION -n "proximity"
tmux send-keys -t $SESSION:2 "$ENV_SETUP && ros2 run orbbec_vision_pkg orbbec_proximity_node" Enter

tmux new-window -t $SESSION -n "memory"
tmux send-keys -t $SESSION:3 "$ENV_SETUP && ros2 run memory_pkg memory_node" Enter

tmux new-window -t $SESSION -n "mic"
tmux send-keys -t $SESSION:4 "$ENV_SETUP && ros2 run audio_pkg mic_node" Enter

tmux new-window -t $SESSION -n "gemini"
tmux send-keys -t $SESSION:5 "$ENV_SETUP && ros2 run reasoning_pkg gemini_live_node" Enter

echo "Sistema MAXCIM iniciado. Adjuntarse con: tmux attach -t maxcim"
echo "Ventanas: 0:camera  1:faces  2:proximity  3:memory  4:mic  5:gemini"
