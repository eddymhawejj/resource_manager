#!/bin/sh
set -e

# Initialize DB + default admin on first run
flask init-db

# Default workers: 2x CPU cores + 1.
# Override with GUNICORN_WORKERS env var.
if [ -z "$GUNICORN_WORKERS" ]; then
    CPU_COUNT=$(nproc 2>/dev/null || echo 1)
    GUNICORN_WORKERS=$(( CPU_COUNT * 2 + 1 ))
fi

echo "Starting gunicorn with $GUNICORN_WORKERS workers"

# Start the app with gunicorn (gevent worker for WebSocket fallback support)
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    --workers "$GUNICORN_WORKERS" \
    --timeout 300 \
    --access-logfile - \
    run:app
