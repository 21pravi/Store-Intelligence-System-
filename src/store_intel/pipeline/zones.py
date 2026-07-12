"""
Polygon zone presence + dwell tracking.

For browse / checkout / staff zones we don't care about line crossings; we care
about *who is inside the zone and for how long*. This module answers, per frame,
which tracks are inside which zone, and accumulates dwell time per (zone, track).
Dwell is what powers engagement metrics, the "reached till" funnel stage, and the
loss-prevention anomaly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from ..config import Zone

Point = Tuple[float, float]


def point_in_polygon(pt: Point, poly: List[Point]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


@dataclass
class DwellRecord:
    zone: str
    track_id: int
    first_ts: float
    last_ts: float
    frames: int = 0

    @property
    def dwell_s(self) -> float:
        return max(0.0, self.last_ts - self.first_ts)


@dataclass
class ZonePresenceTracker:
    """Tracks presence + dwell for one camera's zones."""

    zones: List[Zone]
    # (zone_name, track_id) -> DwellRecord
    records: Dict[Tuple[str, int], DwellRecord] = field(default_factory=dict)

    def update(self, track_id: int, centroid: Point, ts: float) -> List[str]:
        """Register a track at `centroid`; return zones it is currently inside."""
        inside_now: List[str] = []
        for z in self.zones:
            if point_in_polygon(centroid, z.polygon):
                inside_now.append(z.name)
                key = (z.name, track_id)
                rec = self.records.get(key)
                if rec is None:
                    self.records[key] = DwellRecord(z.name, track_id, ts, ts, 1)
                else:
                    rec.last_ts = ts
                    rec.frames += 1
        return inside_now

    def zone_type(self, zone_name: str) -> str:
        for z in self.zones:
            if z.name == zone_name:
                return z.type
        return "unknown"

    def all_records(self) -> List[DwellRecord]:
        return list(self.records.values())
