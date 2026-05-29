#!/bin/sh
set -e

# Graceful shutdown: forward TERM/INT to both uvicorn children
cleanup() {
    echo "Shutting down..."
    kill "$PID4" "$PID6" 2>/dev/null
    wait "$PID4" "$PID6" 2>/dev/null
    exit 0
}
trap cleanup TERM INT

# Start IPv4 listener
python -m uvicorn backend.main:app --host 0.0.0.0 --port "${SERVER_PORT}" &
PID4=$!

# Start IPv6 listener
python -m uvicorn backend.main:app --host :: --port "${SERVER_PORT}" &
PID6=$!

# Wait for either to exit
wait -n "$PID4" "$PID6" 2>/dev/null || wait
