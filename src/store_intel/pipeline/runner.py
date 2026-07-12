"""Orchestrates per-camera workers and writes results to the event store."""
from __future__ import annotations

import logging
import os
from typing import List, Optional

from ..config import AppConfig
from ..detectors import build_detector
from ..store.db import EventStore
from .camera_worker import CameraWorker

log = logging.getLogger("store_intel.runner")


def run_ingestion(cfg: AppConfig, video_dir: str, store: EventStore,
                  cameras: Optional[List[str]] = None,
                  reset: bool = True) -> dict:
    if reset:
        store.reset()

    detector = build_detector(cfg.detector)
    backend = detector.name
    cam_ids: List[str] = []
    total_events = 0

    targets = [c for c in cfg.cameras if (cameras is None or c.id in cameras)]
    for cam in targets:
        path = os.path.join(video_dir, cam.file)
        if not os.path.exists(path):
            log.warning("skip %s: file not found (%s)", cam.id, path)
            continue
        worker = CameraWorker(cam, detector, cfg.detector, cfg.staff_rules)
        result = worker.run(path)
        total_events += store.insert_events(result.events)
        if result.heatmap is not None:
            store.save_heatmap(cam.id, result.heatmap)
        cam_ids.append(cam.id)

    store.record_run(backend=backend, cameras=cam_ids, n_events=total_events,
                     notes=f"video_dir={video_dir}")
    log.info("Ingestion complete: backend=%s cameras=%s events=%d",
             backend, cam_ids, total_events)
    return {"backend": backend, "cameras": cam_ids, "n_events": total_events}
