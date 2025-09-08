#!/bin/bash

# Start the background sync scheduler
echo "Starting background scheduler..."
python /app/main.py &

# Start the Flask Web UI in the foreground
echo "Starting Web UI..."
exec python /app/webui/app.py
