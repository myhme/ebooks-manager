#!/usr/bin/env bash
set -e

: "${FLASK_HOST:=0.0.0.0}"
: "${FLASK_PORT:=5002}"
: "${UVICORN_WORKERS:=2}"

echo "🚀 Starting ebooks-manager (DEBUG=$DEBUG)"

# 1. Run bootstrap
echo "📦 Running bootstrap..."
if ! python /app/src/bootstrap.py; then
    echo "❌ Bootstrap failed, exiting."
    exit 1
fi

# 2. Ensure config.json exists
if [ ! -f /app/config/config.json ]; then
    cp /app/config/config.json.template /app/config/config.json
    echo "⚠️ Created /app/config/config.json from template."
    echo "👉 Please update it with your credentials or override via environment variables."
    exit 1
fi

# 3. Start processes
if [ "$DEBUG" = "true" ]; then
    echo "🐞 Debug mode enabled"

    # WebUI with debugpy (port 5678)
    echo "➡️  Starting WebUI under debugpy on port 5678..."
    python -m debugpy --listen 0.0.0.0:5678 --wait-for-client \
        -m uvicorn src.webui.app:app \
        --host "$FLASK_HOST" \
        --port "$FLASK_PORT" \
        --reload &

    # Scheduler with debugpy (port 5679)
    echo "➡️  Starting Scheduler under debugpy on port 5679..."
    exec python -m debugpy --listen 0.0.0.0:5679 --wait-for-client \
        /app/src/job_runner.py
else
    echo "⚡ Normal mode"

    # WebUI normally (background)
    echo "➡️  Starting WebUI..."
    uvicorn src.webui.app:app \
        --host "$FLASK_HOST" \
        --port "$FLASK_PORT" \
        --workers "$UVICORN_WORKERS" &

    # Scheduler in foreground
    echo "➡️  Starting Scheduler..."
    exec python3 /app/src/job_runner.py
fi
