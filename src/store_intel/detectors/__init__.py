"""Detector factory — resolves the `auto`/`yolo`/`motion` backend choice."""
from __future__ import annotations

import logging

from .base import Detection, Detector
from .motion_detector import MotionDetector
from .yolo_detector import YoloDetector

log = logging.getLogger("store_intel.detectors")


def build_detector(detector_cfg: dict) -> Detector:
    backend = detector_cfg.get("backend", "auto")
    model = detector_cfg.get("yolo_model", "yolov8n.pt")
    conf = float(detector_cfg.get("conf", 0.3))
    imgsz = int(detector_cfg.get("proc_width", 640))

    if backend == "motion":
        log.info("Using MotionDetector (configured).")
        return MotionDetector()

    if backend == "yolo":
        log.info("Using YoloDetector (configured).")
        return YoloDetector(model_path=model, conf=conf, imgsz=imgsz)

    # auto: prefer YOLO if weights are genuinely available, else motion.
    if YoloDetector.is_available(model):
        log.info("auto -> YoloDetector (weights available).")
        try:
            return YoloDetector(model_path=model, conf=conf, imgsz=imgsz)
        except Exception as exc:  # pragma: no cover - env dependent
            log.warning("YOLO load failed (%s); falling back to motion.", exc)
    log.info("auto -> MotionDetector (YOLO weights unavailable).")
    return MotionDetector()


__all__ = ["Detection", "Detector", "MotionDetector", "YoloDetector", "build_detector"]
