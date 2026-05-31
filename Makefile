# Store Intelligence — common tasks
PY ?= python3
export PYTHONPATH := src
VIDEO_DIR ?= ./videos
DB ?= ./data/events.db

.PHONY: help install install-yolo ingest serve test docker-up docker-down clean

help:
	@echo "make install       install base runtime deps (motion backend)"
	@echo "make install-yolo  add YOLOv8 production backend"
	@echo "make ingest        process VIDEO_DIR=$(VIDEO_DIR) -> $(DB)"
	@echo "make serve         run the API + dashboard on :8000"
	@echo "make test          run the pytest suite"
	@echo "make docker-up     build & run via docker compose"
	@echo "make docker-down   stop the stack"

install:
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

install-yolo:
	$(PY) -m pip install -r requirements-yolo.txt

ingest:
	$(PY) scripts/ingest.py --videos $(VIDEO_DIR) --db $(DB)

serve:
	$(PY) -m uvicorn store_intel.api.app:app --host 0.0.0.0 --port 8000 --reload

test:
	$(PY) -m pytest

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	rm -f $(DB); find . -name __pycache__ -type d -exec rm -rf {} +
