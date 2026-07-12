# Store Intelligence — single-stage image (motion backend, torch-free).
FROM python:3.12-slim

# OpenCV (headless) + video decode need a couple of system libs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY data/ ./data/
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV PYTHONPATH=/app/src \
    EVENTS_DB=/app/data/events.db \
    POS_CSV=/app/data/pos.csv \
    STORE_CONFIG=/app/config/store_config.yaml \
    VIDEO_DIR=/app/videos

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=4s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]
