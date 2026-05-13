FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code, system prompt and static dashboard assets
COPY server.py vms.py credentials.py ssh_tools.py ping_tools.py exec_log.py \
     monitor.py dashboard.py system_prompt.md ./
COPY static/ ./static/

# Create data directory structure (will be overridden by volume mount)
RUN mkdir -p /app/data/output

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8765/health || exit 1

CMD ["python", "server.py"]
