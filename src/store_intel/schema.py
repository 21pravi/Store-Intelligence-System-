"""
Unified event schema for the Store Intelligence pipeline.

Every camera worker emits the SAME flat event shape regardless of detector
backend (YOLO or motion). Downstream fusion/analytics only ever read events —
they never touch pixels. This is the contract that keeps the system modular and
testable, and it is what the `/events` API returns verbatim.

Design notes
------------
* One event == one *observation* about one anonymous track at one instant.
  We deliberately store NO biometric/identity data — only an ephemeral per-camera
  integer track id (e.g. "CAM_3#42"). See CHOICES.md (privacy).
* `ts` is wall-clock (camera OSD start_time + frame offset) so events from
  different cameras share a clock and can be fused on time.
* Coordinates are normalised (0..1) so events are resolution-independent.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class EventType(str, enum.Enum):
    """What kind of observation this event records."""

    ENTRY = "entry"                 # track crossed entrance tripwire INTO the store
    EXIT = "exit"                   # track crossed entrance tripwire OUT of the store
    ZONE_PRESENCE = "zone_presence"  # track present in a browse zone (carries dwell)
    CHECKOUT_PRESENCE = "checkout_presence"  # track present in checkout queue zone
    STAFF_PRESENCE = "staff_presence"        # track present in a staff-only zone


class PersonClass(str, enum.Enum):
    CUSTOMER = "customer"
    STAFF = "staff"
    UNKNOWN = "unknown"


class BBox(BaseModel):
    """Normalised bounding box (top-left origin)."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(ge=0.0, le=1.0)
    h: float = Field(ge=0.0, le=1.0)

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


class Event(BaseModel):
    """A single structured observation in the store event stream."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime                          # wall-clock of the observation
    camera_id: str
    zone: str                             # logical zone name (entrance, makeup_aisle, ...)
    event_type: EventType
    track_id: str                         # ephemeral, per-camera (e.g. "CAM_3#42")
    person_class: PersonClass = PersonClass.UNKNOWN
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    bbox: Optional[BBox] = None
    dwell_s: float = Field(default=0.0, ge=0.0)  # seconds present (presence events)
    meta: dict = Field(default_factory=dict)

    @field_validator("camera_id", "zone", "track_id")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not str(v).strip():
            raise ValueError("must be a non-empty string")
        return v

    def to_row(self) -> dict:
        """Flatten to a DB-friendly row (bbox split out, ts as ISO)."""
        b = self.bbox
        return {
            "event_id": self.event_id,
            "ts": self.ts.isoformat(),
            "camera_id": self.camera_id,
            "zone": self.zone,
            "event_type": self.event_type.value,
            "track_id": self.track_id,
            "person_class": self.person_class.value,
            "confidence": round(self.confidence, 4),
            "bbox_x": None if b is None else round(b.x, 5),
            "bbox_y": None if b is None else round(b.y, 5),
            "bbox_w": None if b is None else round(b.w, 5),
            "bbox_h": None if b is None else round(b.h, 5),
            "dwell_s": round(self.dwell_s, 3),
            "meta": self.meta,
        }
