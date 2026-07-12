"""
Production detector: Ultralytics YOLOv8 person detection + ByteTrack.

Imports of ultralytics/torch are done lazily inside __init__ so that the rest of
the system (and the test-suite) never pays for them and can run with the motion
backend alone. `is_available()` lets the factory decide whether YOLO can load
before committing to it.
"""
from __future__ import annotations

from typing import List

import numpy as np

from .base import Detection

PERSON_CLASS = 0  # COCO 'person'


class YoloDetector:
    name = "yolo"

    def __init__(self, model_path: str = "yolov8n.pt", conf: float = 0.3,
                 imgsz: int = 640):
        from ultralytics import YOLO  # lazy
        self.model = YOLO(model_path)
        self.conf = conf
        self.imgsz = imgsz

    @staticmethod
    def is_available(model_path: str = "yolov8n.pt") -> bool:
        """True only if ultralytics imports AND weights can be obtained."""
        try:
            import importlib.util
            import os
            if importlib.util.find_spec("ultralytics") is None:
                return False
            # Weights present locally? (We never trigger a network download here.)
            if os.path.exists(model_path):
                return True
            # Common cache locations.
            for base in (os.getcwd(), os.path.expanduser("~/.cache/ultralytics")):
                if os.path.exists(os.path.join(base, model_path)):
                    return True
            return False
        except Exception:
            return False

    def detect(self, frame: np.ndarray) -> List[Detection]:
        H, W = frame.shape[:2]
        # persist=True keeps ByteTrack IDs stable across calls on one stream.
        res = self.model.track(
            frame, classes=[PERSON_CLASS], conf=self.conf, imgsz=self.imgsz,
            persist=True, verbose=False, tracker="bytetrack.yaml",
        )[0]
        out: List[Detection] = []
        if res.boxes is None:
            return out
        for b in res.boxes:
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0]]
            tid = int(b.id[0]) if b.id is not None else None
            out.append(Detection(
                x=x1 / W, y=y1 / H, w=(x2 - x1) / W, h=(y2 - y1) / H,
                confidence=float(b.conf[0]) if b.conf is not None else 1.0,
                track_id=tid,
            ))
        return out
