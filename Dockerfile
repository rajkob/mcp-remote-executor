FROM python:3.12-slim

WORKDIR /app

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

CMD ["python", "server.py"]
