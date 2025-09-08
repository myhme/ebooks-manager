# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# --- Install Dependencies ---
# Install essential tools plus 'jq' for parsing JSON from web APIs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    unzip \
    ca-certificates \
    jq \
    # Add dependencies for Chrome
    libglib2.0-0 libnss3 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# --- Install Latest Stable Google Chrome and ChromeDriver ---
# This block dynamically finds and downloads the latest stable versions to prevent build failures.
RUN CHROME_VERSION_URL="https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json" && \
    CHROME_URL=$(wget -q -O - "$CHROME_VERSION_URL" | jq -r '.channels.Stable.downloads.chrome[] | select(.platform=="linux64") | .url') && \
    CHROMEDRIVER_URL=$(wget -q -O - "$CHROME_VERSION_URL" | jq -r '.channels.Stable.downloads.chromedriver[] | select(.platform=="linux64") | .url') && \
    \
    # Download and install Google Chrome by unzipping it
    wget --no-verbose -O /tmp/chrome.zip "$CHROME_URL" && \
    unzip /tmp/chrome.zip -d /opt && \
    ln -s /opt/chrome-linux64/chrome /usr/bin/google-chrome && \
    rm /tmp/chrome.zip && \
    \
    # Download and install ChromeDriver
    wget --no-verbose -O /tmp/chromedriver.zip "$CHROMEDRIVER_URL" && \
    unzip /tmp/chromedriver.zip -d /usr/local/bin/ && \
    mv /usr/local/bin/chromedriver-linux64/chromedriver /usr/local/bin/chromedriver && \
    chmod +x /usr/local/bin/chromedriver && \
    rm -rf /usr/local/bin/chromedriver-linux64 && \
    rm /tmp/chromedriver.zip

# --- Install Python Dependencies ---
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Copy Application Code and Entrypoint ---
COPY src/ .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# The command to run when the container starts
ENTRYPOINT ["./entrypoint.sh"]
