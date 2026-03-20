#!/bin/sh
set -e

# Initialize DB + default admin on first run
flask init-db

# Start the app with gunicorn (gevent worker for WebSocket fallback support)
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    --workers 1 \
    --timeout 300 \
    --access-logfile - \
    run:app
