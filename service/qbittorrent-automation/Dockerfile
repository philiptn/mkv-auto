FROM python:3.11-slim

# Create non-root user
RUN useradd -m -u 1000 service

# Set working directory
WORKDIR /service

# Copy Python script only (no config)
COPY qbittorrent-automation.py /service/

# Install dependencies
RUN pip install --no-cache-dir requests

# Switch to non-root user
USER service

# Run script
CMD ["python", "/service/qbittorrent-automation.py"]