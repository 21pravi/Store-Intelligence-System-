"""Detector backend interface.

A detector turns a single video frame into a list of `Detection`s (person boxes
with an optional already-assigned track id). Two implementations exist:

* MotionDetector  — OpenCV MOG2 background subtraction. Zero downloads, runs
                    anywhere. Used as the fallback / CI / sandbox backend.
* YoloDetector    — Ultralytics YOLOv8 + ByteTrack. Production default; needs
                    model weights (auto-downloaded on first run).

Keeping this seam means the rest of the system never imports torch or cv2 detail
and is fully unit-testable with synthetic detections.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

import numpy as np


@dataclass
class Detection:
    # Normalised box (0..1), top-left origin.
    x: float
    y: float
    w: float
    h: float
    confidence: float = 1.0
    track_id: Optional[int] = None   # set if the backend tracks internally (YOLO)

    @property
    def box(self):
        return (self.x, self.y, self.w, self.h)


class Detector(Protocol):
    name: str

    def detect(self, frame: "np.ndarray") -> List[Detection]:
        """Return person detections for one BGR frame."""
        ...
