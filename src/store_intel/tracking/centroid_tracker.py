"""
Lightweight multi-object tracker (a SORT-lite).

Greedy IoU + centroid-distance association with a max-age tolerance for short
occlusions. Good enough for footfall through a doorway and for zone presence;
it is the tracker used by the dependency-free `motion` backend. The `yolo`
backend can use ultralytics' built-in ByteTrack instead (see CHOICES.md).

No appearance model is used — tracks are anonymous and ephemeral.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Box = Tuple[float, float, float, float]  # x, y, w, h (normalised)


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2, bx2, by2 = ax + aw, ay + ah, bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _centroid(b: Box) -> Tuple[float, float]:
    return b[0] + b[2] / 2.0, b[1] + b[3] / 2.0


def _dist(a: Box, b: Box) -> float:
    (ax, ay), (bx, by) = _centroid(a), _centroid(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


@dataclass
class Track:
    track_id: int
    box: Box
    centroid: Tuple[float, float]
    age: int = 0            # frames since last seen
    hits: int = 1           # total observations
    history: List[Tuple[float, float]] = field(default_factory=list)


class CentroidTracker:
    def __init__(self, max_age: int = 12, iou_thresh: float = 0.1,
                 max_dist: float = 0.20):
        self.max_age = max_age
        self.iou_thresh = iou_thresh
        self.max_dist = max_dist
        self._next_id = 1
        self.tracks: Dict[int, Track] = {}

    def update(self, detections: List[Box]) -> Dict[int, Track]:
        """Advance one frame. Returns the dict of currently *live* tracks."""
        # Age everything first.
        for t in self.tracks.values():
            t.age += 1

        unmatched = set(range(len(detections)))
        # Greedy match: best (highest IoU, else nearest centroid) pairs first.
        pairs: List[Tuple[float, int, int]] = []  # (-score, track_id, det_idx)
        for tid, tr in self.tracks.items():
            for di, det in enumerate(detections):
                iou = _iou(tr.box, det)
                d = _dist(tr.box, det)
                if iou >= self.iou_thresh or d <= self.max_dist:
                    # score: prefer high IoU; fall back to proximity.
                    score = iou + (1.0 - min(d / self.max_dist, 1.0)) * 0.5
                    pairs.append((-score, tid, di))
        pairs.sort()

        matched_tracks: set = set()
        for _, tid, di in pairs:
            if tid in matched_tracks or di not in unmatched:
                continue
            tr = self.tracks[tid]
            tr.box = detections[di]
            tr.centroid = _centroid(detections[di])
            tr.history.append(tr.centroid)
            tr.age = 0
            tr.hits += 1
            matched_tracks.add(tid)
            unmatched.discard(di)

        # New tracks for unmatched detections.
        for di in unmatched:
            box = detections[di]
            t = Track(self._next_id, box, _centroid(box))
            t.history.append(t.centroid)
            self.tracks[self._next_id] = t
            self._next_id += 1

        # Drop stale tracks.
        dead = [tid for tid, t in self.tracks.items() if t.age > self.max_age]
        for tid in dead:
            del self.tracks[tid]

        return {tid: t for tid, t in self.tracks.items() if t.age == 0}
