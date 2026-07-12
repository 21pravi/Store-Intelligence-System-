#!/usr/bin/env python3
"""
CLI: ingest CCTV footage -> structured events in SQLite.

Usage:
    python scripts/ingest.py --videos ./videos --db ./data/events.db
    python scripts/ingest.py --cameras CAM_3 CAM_5 --backend motion
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store_intel.config import load_config          # noqa: E402
from store_intel.pipeline.runner import run_ingestion  # noqa: E402
from store_intel.store.db import EventStore         # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest CCTV footage into events.db")
    ap.add_argument("--videos", default=os.environ.get("VIDEO_DIR", "./videos"))
    ap.add_argument("--db", default=os.environ.get("EVENTS_DB", "./data/events.db"))
    ap.add_argument("--config", default=os.environ.get("STORE_CONFIG", None))
    ap.add_argument("--cameras", nargs="*", default=None,
                    help="subset of camera ids (default: all)")
    ap.add_argument("--backend", choices=["auto", "yolo", "motion"], default=None)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    cfg = load_config(args.config)
    if args.backend:
        cfg.detector["backend"] = args.backend

    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    store = EventStore(args.db)
    summary = run_ingestion(cfg, args.videos, store, cameras=args.cameras,
                            reset=not args.no_reset)
    print("INGEST SUMMARY:", summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
