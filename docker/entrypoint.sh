#!/usr/bin/env bash
set -euo pipefail

# Re-ingest only when explicitly asked, or when there is no event store yet but
# footage IS mounted. Otherwise serve the bundled, pre-computed events.db so that
# `docker compose up` shows real data instantly (acceptance gate friendly).
have_videos() { ls "${VIDEO_DIR}"/*.mp4 >/dev/null 2>&1; }

if [[ "${FORCE_INGEST:-0}" == "1" ]] && have_videos; then
    echo "[entrypoint] FORCE_INGEST=1 -> ingesting footage from ${VIDEO_DIR}"
    python scripts/ingest.py --videos "${VIDEO_DIR}" --db "${EVENTS_DB}" \
        --backend "${DETECTOR_BACKEND:-auto}"
elif [[ ! -f "${EVENTS_DB}" ]] && have_videos; then
    echo "[entrypoint] no event store found -> first-run ingestion from ${VIDEO_DIR}"
    python scripts/ingest.py --videos "${VIDEO_DIR}" --db "${EVENTS_DB}" \
        --backend "${DETECTOR_BACKEND:-auto}"
else
    echo "[entrypoint] serving existing event store: ${EVENTS_DB}"
fi

exec uvicorn store_intel.api.app:app --host 0.0.0.0 --port 8000
