FROM python:3.11-slim

# Create non-root user
RUN useradd -m -u 1000 appuser

# Set working directory
WORKDIR /app

# Copy Python script only (no config)
COPY qbittorrent-automation.py /app/

# Install dependencies
RUN pip install --no-cache-dir requests

# Switch to non-root user
USER appuser

# Run script
CMD ["python", "/app/qbittorrent-automation.py"]