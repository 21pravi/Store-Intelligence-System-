"""
Virtual tripwire for entrance counting.

Detects when a track's centroid crosses a configured line and classifies the
crossing as an ENTRY or EXIT by direction. Includes:

* a minimum displacement gate (ignores jitter that straddles the line),
* per-track debounce (a track loitering on the line is counted once),
* re-entry handling: the SAME track crossing in the same direction again within
  the debounce window is suppressed (no double counting). A genuine re-entry
  after the window is a new, legitimate entry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..config import Tripwire

Point = Tuple[float, float]


def _side(p1: Point, p2: Point, pt: Point) -> float:
    """Signed side of point `pt` relative to directed line p1->p2."""
    return (p2[0] - p1[0]) * (pt[1] - p1[1]) - (p2[1] - p1[1]) * (pt[0] - p1[0])


@dataclass
class _TrackState:
    last_point: Point
    last_cross_ts: float = -1e9
    last_cross_dir: Optional[str] = None


@dataclass
class Crossing:
    track_id: int
    direction: str          # "entry" | "exit"
    point: Point
    ts: float


@dataclass
class TripwireCounter:
    tw: Tripwire
    entries: int = 0
    exits: int = 0
    _state: Dict[int, _TrackState] = field(default_factory=dict)

    def _direction_of_motion(self, prev: Point, cur: Point) -> str:
        dx, dy = cur[0] - prev[0], cur[1] - prev[1]
        if abs(dx) >= abs(dy):
            return "left" if dx < 0 else "right"
        return "up" if dy < 0 else "down"

    def update(self, track_id: int, centroid: Point, ts: float) -> Optional[Crossing]:
        st = self._state.get(track_id)
        if st is None:
            self._state[track_id] = _TrackState(last_point=centroid)
            return None

        prev = st.last_point
        s_prev = _side(self.tw.p1, self.tw.p2, prev)
        s_cur = _side(self.tw.p1, self.tw.p2, centroid)
        st.last_point = centroid

        # Did we change sides of the line?
        if s_prev == 0 or s_cur == 0 or (s_prev > 0) == (s_cur > 0):
            return None

        # Enough perpendicular travel to be a real crossing, not jitter.
        disp = ((centroid[0] - prev[0]) ** 2 + (centroid[1] - prev[1]) ** 2) ** 0.5
        if disp < self.tw.min_cross_disp:
            return None

        motion = self._direction_of_motion(prev, centroid)
        direction = "entry" if motion == self.tw.entry_direction else "exit"

        # Debounce: any crossing by the SAME track within the window is treated as
        # loitering jitter on the line and suppressed (different people have
        # different track ids, so group arrivals are unaffected). The timer only
        # advances on a counted crossing, so a genuine pass-through after a quiet
        # gap is always counted.
        if ts - st.last_cross_ts < self.tw.debounce_seconds:
            return None

        st.last_cross_ts = ts
        st.last_cross_dir = direction
        if direction == "entry":
            self.entries += 1
        else:
            self.exits += 1
        return Crossing(track_id=track_id, direction=direction, point=centroid, ts=ts)

    def net_occupancy(self) -> int:
        return self.entries - self.exits
