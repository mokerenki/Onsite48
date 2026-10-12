# SynthAI Backend — Production Dockerfile
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends     gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# Cognee depends on langdetect, which is published without a compatible wheel.
# Install that pure-Python dependency first while keeping binary-only installs
# for the remaining production packages.
RUN pip install --no-cache-dir --timeout=60 "langdetect>=1.0.9"
RUN pip install --no-cache-dir --timeout=60 --only-binary :all: -r requirements.txt
RUN playwright install-deps chromium

COPY . .
RUN mkdir -p /tmp/synthai/sandbox
RUN chmod +x /app/entrypoint.sh
RUN useradd -m -u 1000 synthai && chown -R synthai:synthai /app /tmp/synthai
USER synthai
# Persist Playwright browsers in the synthai_kuzu volume so they survive
# container recreation. The volume is mounted at /tmp/synthai by compose.
ENV PLAYWRIGHT_BROWSERS_PATH=/tmp/synthai/.cache/ms-playwright
RUN mkdir -p /tmp/synthai/.cache/ms-playwright && playwright install chromium || true

EXPOSE 8000
CMD ["/app/entrypoint.sh"]