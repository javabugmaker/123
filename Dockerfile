# InstitutionScanner Docker Image
# Build:  docker build -t institution-scanner .
# Run:    docker run --rm -v "%cd%/cache:/app/cache" -v "%cd%/output:/app/output" -v "%cd%/logs:/app/logs" -it institution-scanner python main.py scan
# Run (interactive shell): docker run --rm -v "%cd%/cache:/app/cache" -v "%cd%/output:/app/output" -v "%cd%/logs:/app/logs" -it institution-scanner

FROM python:3.11-slim-bookworm

LABEL maintainer="institution-scanner"
LABEL description="Institutional Accumulation Scanner — detect stocks/ETFs with bear-market accumulation signals"

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import requests,pandas,numpy; print('OK')" || exit 1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash scanner

WORKDIR /app

# Keep container dependency resolution aligned with the CI/DAILY reviewed set.
COPY requirements.txt constraints-ci.txt ./
RUN pip install --break-system-packages -c constraints-ci.txt -r requirements.txt

COPY . .

RUN mkdir -p /app/cache /app/output /app/logs && \
    chown -R scanner:scanner /app

USER scanner

CMD ["python", "main.py", "scan"]