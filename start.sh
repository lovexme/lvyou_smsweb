#!/bin/bash
# 手动部署启动脚本：同时监听 IPv4 和 IPv6
# 用法: ./start.sh [--port 8000]

set -e

PORT="${1:-${SERVER_PORT:-8000}}"

cleanup() {
    echo "Shutting down..."
    kill "$PID4" "$PID6" 2>/dev/null
    wait "$PID4" "$PID6" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

cd "$(dirname "$0")" || exit 1

echo "Starting on port $PORT (IPv4 + IPv6)..."

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$PORT" &
PID4=$!

python3 -m uvicorn backend.main:app --host :: --port "$PORT" &
PID6=$!

echo "IPv4: http://0.0.0.0:$PORT"
echo "IPv6: http://[::]:$PORT"

while kill -0 "$PID4" 2>/dev/null && kill -0 "$PID6" 2>/dev/null; do
    sleep 1
done
cleanup
