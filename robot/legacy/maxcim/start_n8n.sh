#!/bin/bash

docker rm -f n8n 2>/dev/null

docker run -d \
  --name n8n \
  -p 5678:5678 \
  -e N8N_HOST=0.0.0.0 \
  -e N8N_PORT=5678 \
  -e N8N_PROTOCOL=http \
  -e N8N_SECURE_COOKIE=false \
  -e WEBHOOK_URL=http://192.168.70.236:5678/ \
  -e N8N_EDITOR_BASE_URL=http://192.168.70.236:5678/ \
  -v n8n_data:/home/node/.n8n \
  n8nio/n8n:latest

echo "n8n corriendo en http://<TU_IP_O_DOMINIO>:5678"
