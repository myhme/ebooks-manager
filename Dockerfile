# syntax=docker/dockerfile:1

########################
# Stage 1: Builder
########################
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

########################
# Stage 2: Runtime
########################
FROM python:3.11-slim

WORKDIR /app

# Create non-root user and switch to it
RUN useradd -m -r -u 1000 appuser && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /app /home/appuser
USER appuser

# Install runtime dependencies for the app
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    # ... all other runtime libs you listed
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appuser \
    src/ ./src/ \
    config/ ./config/ \
    scripts/ ./scripts/ \
    requirements.txt ./requirements.txt \
    .env.example ./.env.example \
    src/webui/templates/ ./src/webui/templates/ \
    src/webui/static/ ./src/webui/static/

RUN chmod +x /app/scripts/entrypoint.sh

# Ensure logs dir exists (now as non-root user)
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

# Env vars
ENV PYTHONPATH=/app \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PATH="/home/appuser/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose WebUI
EXPOSE 5002

# Entrypoint
ENTRYPOINT ["/app/scripts/entrypoint.sh"]