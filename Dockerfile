# InstitutionScanner Docker Image
# Build:  docker build -t institution-scanner .
# Run:    docker run --rm -v "%cd%/cache:/app/cache" -v "%cd%/output:/app/output" -v "%cd%/logs:/app/logs" -it institution-scanner python main.py scan
# Run (interactive shell): docker run --rm -v "%cd%/cache:/app/cache" -v "%cd%/output:/app/output" -v "%cd%/logs:/app/logs" -it institution-scanner

FROM python:3.12-slim-bookworm

LABEL maintainer="institution-scanner"
LABEL description="Institutional Accumulation Scanner — detect stocks/ETFs with bear-market accumulation signals"

# Avoid buffering stdout
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

# Health check — verify Python and key modules are functional
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import requests,pandas,numpy; print('OK')" || exit 1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash scanner

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --break-system-packages -r requirements.txt

# Copy the rest of the project
COPY . .

# Create data directories and fix ownership
RUN mkdir -p /app/cache /app/output /app/logs && \
    chown -R scanner:scanner /app

USER scanner

# Default: run a scan — override with docker run args for custom commands
CMD ["python", "main.py", "scan"]
