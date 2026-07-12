"""
Per-camera processing worker: video frames -> structured Events.

Pipeline per camera:
    frame (sampled @ target_fps)
      -> detector.detect()              (YOLO or motion)
      -> tracker.update()               (IDs; YOLO may supply its own)
      -> role-specific logic:
           entrance -> TripwireCounter   -> ENTRY / EXIT events (on crossing)
           browse   -> ZonePresence      -> ZONE_PRESENCE events (finalised w/ dwell)
           checkout -> ZonePresence      -> CHECKOUT_PRESENCE / STAFF_PRESENCE
           backroom -> ZonePresence      -> STAFF_PRESENCE

Timestamps are wall-clock: camera.start_time + frame_index / fps. Each track's
centroid uses the *foot point* (bottom-centre of the box) because that is where a
person actually stands on the floor — far more stable for line/zone tests than
the box centre, which sits at torso height and shifts with pose.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

import cv2
import numpy as np

from ..config import CameraConfig
from ..detectors.base import Detector
from ..schema import BBox, Event, EventType, PersonClass
from ..tracking.centroid_tracker import CentroidTracker
from .tripwire import TripwireCounter
from .zones import ZonePresenceTracker

log = logging.getLogger("store_intel.camera")


@dataclass
class CameraResult:
    camera_id: str
    role: str
    events: List[Event] = field(default_factory=list)
    frames_processed: int = 0
    duration_s: float = 0.0
    entries: int = 0
    exits: int = 0
    heatmap: Optional[np.ndarray] = None  # accumulated presence, normalised 0..1


def _foot_point(d) -> tuple:
    """Bottom-centre of a normalised detection box."""
    return (d.x + d.w / 2.0, d.y + d.h)


class CameraWorker:
    def __init__(self, cam: CameraConfig, detector: Detector, detector_cfg: dict,
                 staff_rules: dict, heatmap_size: int = 64):
        self.cam = cam
        self.detector = detector
        self.target_fps = float(detector_cfg.get("target_fps", 5))
        self.proc_width = int(detector_cfg.get("proc_width", 640))
        self.min_presence_frames = int(detector_cfg.get("min_presence_frames", 1))
        self.max_customer_dwell = float(
            staff_rules.get("max_customer_dwell_seconds", 600))
        self.staff_only_zones = set(staff_rules.get("staff_only_zones", []))
        self.heatmap_size = heatmap_size

        self.tripwire = TripwireCounter(cam.tripwire) if cam.tripwire else None
        self.zones = ZonePresenceTracker(cam.zones) if cam.zones else None
        # CentroidTracker is used when the detector does not supply IDs (motion).
        self.tracker = CentroidTracker()

    def run(self, video_path: str) -> CameraResult:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise FileNotFoundError(f"cannot open video: {video_path}")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        step = max(1, int(round(fps / self.target_fps)))

        res = CameraResult(camera_id=self.cam.id, role=self.cam.role)
        heat = np.zeros((self.heatmap_size, self.heatmap_size), dtype=np.float32)

        i = 0
        while True:
            if not cap.grab():
                break
            if i % step != 0:
                i += 1
                continue
            ok, frame = cap.retrieve()
            if not ok:
                break

            # Downscale for detection speed (normalised coords are unaffected).
            fh, fw = frame.shape[:2]
            if fw > self.proc_width:
                scale = self.proc_width / float(fw)
                frame = cv2.resize(frame, (self.proc_width, int(fh * scale)))

            ts_dt = self.cam.start_time + timedelta(seconds=i / fps)
            ts = ts_dt.timestamp()

            dets = self.detector.detect(frame)

            # Resolve track ids: prefer detector-supplied (YOLO), else CentroidTracker.
            if any(d.track_id is not None for d in dets):
                id_box = [(d.track_id, d) for d in dets if d.track_id is not None]
            else:
                live = self.tracker.update([d.box for d in dets])
                # Map updated tracks back to their detection by centroid match.
                id_box = []
                for tid, tr in live.items():
                    # nearest detection to this track's box
                    best, bestd = None, 1e9
                    for d in dets:
                        dd = (tr.centroid[0] - (d.x + d.w / 2)) ** 2 + \
                             (tr.centroid[1] - (d.y + d.h / 2)) ** 2
                        if dd < bestd:
                            best, bestd = d, dd
                    if best is not None:
                        id_box.append((tid, best))

            for tid, d in id_box:
                fp = _foot_point(d)
                # Heatmap accumulation (foot point).
                hx = min(self.heatmap_size - 1, max(0, int(fp[0] * self.heatmap_size)))
                hy = min(self.heatmap_size - 1, max(0, int(fp[1] * self.heatmap_size)))
                heat[hy, hx] += 1.0

                bbox = BBox(x=d.x, y=d.y, w=d.w, h=d.h)

                if self.tripwire is not None:
                    crossing = self.tripwire.update(tid, fp, ts)
                    if crossing is not None:
                        et = EventType.ENTRY if crossing.direction == "entry" else EventType.EXIT
                        res.events.append(Event(
                            ts=ts_dt, camera_id=self.cam.id, zone=self.cam.zone,
                            event_type=et, track_id=f"{self.cam.id}#{tid}",
                            person_class=PersonClass.UNKNOWN, confidence=d.confidence,
                            bbox=bbox, meta={"point": [round(fp[0], 4), round(fp[1], 4)]},
                        ))

                if self.zones is not None:
                    self.zones.update(tid, fp, ts)

            res.frames_processed += 1
            i += 1

        cap.release()
        res.duration_s = (total / fps) if fps else 0.0

        # Finalise tripwire counts.
        if self.tripwire is not None:
            res.entries = self.tripwire.entries
            res.exits = self.tripwire.exits

        # Finalise zone presence -> one event per (zone, track) with dwell.
        if self.zones is not None:
            for rec in self.zones.all_records():
                # Drop transient noise blobs that never persisted.
                if rec.frames < self.min_presence_frames:
                    continue
                ztype = self.zones.zone_type(rec.zone)
                is_staff_zone = rec.zone in self.staff_only_zones or ztype == "staff_only"
                long_dwell = rec.dwell_s > self.max_customer_dwell
                if is_staff_zone:
                    pclass = PersonClass.STAFF
                    etype = EventType.STAFF_PRESENCE
                elif ztype == "checkout":
                    pclass = PersonClass.CUSTOMER
                    etype = EventType.CHECKOUT_PRESENCE
                else:  # browse
                    pclass = PersonClass.STAFF if long_dwell else PersonClass.CUSTOMER
                    etype = EventType.ZONE_PRESENCE
                start_dt = self.cam.start_time + timedelta(
                    seconds=rec.first_ts - self.cam.start_time.timestamp())
                res.events.append(Event(
                    ts=start_dt, camera_id=self.cam.id, zone=rec.zone,
                    event_type=etype, track_id=f"{self.cam.id}#{rec.track_id}",
                    person_class=pclass, confidence=0.6, dwell_s=rec.dwell_s,
                    meta={"frames": rec.frames, "zone_type": ztype},
                ))

        mx = float(heat.max())
        res.heatmap = (heat / mx) if mx > 0 else heat
        log.info("[%s/%s] frames=%d entries=%d exits=%d events=%d",
                 self.cam.id, self.cam.role, res.frames_processed,
                 res.entries, res.exits, len(res.events))
        return res
