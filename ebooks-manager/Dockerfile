FROM python:3.9-slim

WORKDIR /app

# Create non-root user
RUN useradd -m -r -u 1000 appuser && \
    mkdir -p /home/appuser/.cache && \
    chown -R appuser:appuser /app /home/appuser

# Install essential packages including chromium and driver
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg unzip ca-certificates jq chromium chromium-driver \
    libglib2.0-0 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Ensure chromedriver permissions
RUN if [ ! -f /usr/bin/chromedriver ]; then echo "ChromeDriver missing"; exit 1; fi && \
    chmod 755 /usr/bin/chromedriver && chown appuser:appuser /usr/bin/chromedriver

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && pip cache purge

# Copy app
COPY . .

# Ensure logs/data directories are writable
RUN mkdir -p /app/logs /app/data && chown -R appuser:appuser /app

ENV PYTHONPATH=/app \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver \
    PATH="/home/appuser/.local/bin:$PATH" \
    TZ=Asia/Kolkata

USER appuser

CMD ["/app/scripts/entrypoint.sh"]
