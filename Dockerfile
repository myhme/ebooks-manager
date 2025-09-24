# syntax=docker/dockerfile:1

########################
# Stage 1: Builder
########################
FROM python:3.11-slim AS builder

WORKDIR /app

# System deps (Chromium + Selenium requirements)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    unzip \
    ca-certificates \
    jq \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    build-essential \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python deps into a clean layer
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    pip cache purge

########################
# Stage 2: Runtime
########################
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN useradd -m -r -u 1000 appuser && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /app /home/appuser

# Install runtime Chromium + minimal system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    libglib2.0-0 \
    libnss3 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
 && apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy installed site-packages from builder
COPY --from=builder /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=builder /usr/local/bin /usr/local/bin

# Whitelist COPY (only app code)
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY requirements.txt ./requirements.txt
COPY .env.example ./.env.example
COPY src/webui/templates/ ./src/webui/templates/
COPY src/webui/static/ ./src/webui/static/

RUN chmod +x /app/scripts/entrypoint.sh

# Ensure logs dir exists
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

# Env vars
ENV PYTHONPATH=/app \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PATH="/home/appuser/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Switch to non-root
USER appuser

# Expose WebUI
EXPOSE 5002

# Entrypoint
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
