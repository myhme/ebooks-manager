#!/bin/bash
set -euo pipefail

# Entrypoint: run migrations/bootstrap, start gunicorn and run scheduler in background
echo "Running bootstrap..."
if ! python /app/src/bootstrap.py; then
    echo "Bootstrap failed, exiting."
    exit 1
fi

# Ensure config exists
if [ ! -f /app/config/config.json ]; then
    if [ -f /app/config/config.json.template ]; then
        cp /app/config/config.json.template /app/config/config.json
        echo "Created config.json from template. Please update it with your credentials or use environment variables."
        exit 1
    else
        echo "Missing config.json.template; please provide config/config.json."
        exit 1
    fi
fi

# Start Gunicorn (foreground) and scheduler as background process via job runner
echo "Starting job runner (scheduler) in background..."
python3 /app/src/job_runner.py &

echo "Starting Gunicorn..."
exec gunicorn --bind 0.0.0.0:5002 --workers 2 --threads 4 --timeout 120 --log-file /app/logs/gunicorn.log --log-level info src.webui.app:app
