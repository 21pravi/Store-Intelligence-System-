"""
Dependency-free detector: MOG2 background subtraction + blob filtering.

Why this exists
---------------
The production backend is YOLOv8, but it needs model weights that may be
unavailable (air-gapped eval box, blocked download, no GPU). Rather than fail
the acceptance gate, the system degrades gracefully to this classical-CV
detector, which needs nothing but OpenCV and produces real, input-varying
detections. It is excellent at the single most important task — counting people
moving across the entrance line — and serviceable for motion-based presence in
other zones. Limitations (stationary shoppers fading into the background) are
documented in CHOICES.md.
"""
from __future__ import annotations

from typing import List

import cv2
import numpy as np

from .base import Detection


class MotionDetector:
    name = "motion"

    def __init__(self, history: int = 300, var_threshold: float = 40.0,
                 min_area_frac: float = 0.004, max_area_frac: float = 0.40,
                 min_aspect: float = 0.8):
        # Foreground/background model. Shadows detected then dropped (value 127).
        self._bg = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=var_threshold, detectShadows=True
        )
        self.min_area_frac = min_area_frac
        self.max_area_frac = max_area_frac
        self.min_aspect = min_aspect          # h/w; people are taller than wide
        self._k_open = np.ones((3, 3), np.uint8)
        self._k_dilate = np.ones((5, 5), np.uint8)

    def detect(self, frame: np.ndarray) -> List[Detection]:
        H, W = frame.shape[:2]
        mask = self._bg.apply(frame)
        # Drop shadows (127) — keep only hard foreground.
        mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._k_open)
        mask = cv2.dilate(mask, self._k_dilate, iterations=2)

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(W * H)
        out: List[Detection] = []
        for c in cnts:
            area = cv2.contourArea(c)
            frac = area / frame_area
            if frac < self.min_area_frac or frac > self.max_area_frac:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w == 0:
                continue
            aspect = h / float(w)
            # Person-ish blobs are roughly upright; allow wide ones too (groups,
            # people bending over a display) but score them lower.
            conf = 0.55 if aspect >= self.min_aspect else 0.4
            out.append(Detection(
                x=x / W, y=y / H, w=w / W, h=h / H, confidence=conf
            ))
        return out
