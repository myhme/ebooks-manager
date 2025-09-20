#!/bin/bash
set -e

# Run bootstrap with error logging
echo "Running bootstrap..."
if ! python /app/src/bootstrap.py; then
    echo "Bootstrap failed, exiting."
    exit 1
fi

# Ensure config file exists
if [ ! -f /app/config/config.json ]; then
    cp /app/config/config.json.template /app/config/config.json
    echo "Created config.json from template. Please update it with your credentials or use environment variables."
    exit 1
fi

# Run Gunicorn in the background with logging
echo "Starting Gunicorn..."
gunicorn --bind 0.0.0.0:5002 --workers 1 --threads 2 --timeout 120 --log-file /app/logs/gunicorn.log src.webui.app:app &

# Run the scheduler in the foreground
echo "Starting scheduler..."
exec python3 /app/src/job_runner.py
