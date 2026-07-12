from datetime import datetime

import pytest

from store_intel.config import Tripwire
from store_intel.pipeline.tripwire import TripwireCounter
from store_intel.pipeline.zones import ZonePresenceTracker, point_in_polygon
from store_intel.config import Zone
from store_intel.schema import BBox, Event, EventType
from store_intel.tracking.centroid_tracker import CentroidTracker


# ---------------- schema ----------------
def test_event_roundtrip_row():
    e = Event(ts=datetime(2026, 4, 10, 20, 0), camera_id="CAM_3", zone="entrance",
              event_type=EventType.ENTRY, track_id="CAM_3#1",
              bbox=BBox(x=0.1, y=0.2, w=0.3, h=0.4))
    row = e.to_row()
    assert row["event_type"] == "entry"
    assert row["bbox_w"] == 0.3
    assert row["camera_id"] == "CAM_3"


def test_event_rejects_empty_track():
    with pytest.raises(Exception):
        Event(ts=datetime(2026, 4, 10), camera_id="CAM_3", zone="e",
              event_type=EventType.ENTRY, track_id="  ")


# ---------------- centroid tracker ----------------
def test_tracker_keeps_id_across_frames():
    trk = CentroidTracker()
    b = (0.50, 0.50, 0.05, 0.10)
    live = trk.update([b]); tid = list(live)[0]
    for dx in (0.01, 0.02, 0.03):
        live = trk.update([(0.50 + dx, 0.50, 0.05, 0.10)])
    assert tid in live  # same id persisted while moving


def test_tracker_distinct_ids_for_far_objects():
    trk = CentroidTracker()
    live = trk.update([(0.1, 0.1, 0.05, 0.1), (0.8, 0.8, 0.05, 0.1)])
    assert len(live) == 2


# ---------------- tripwire ----------------
def _tw(direction="left"):
    return TripwireCounter(Tripwire(p1=(0.5, 0.0), p2=(0.5, 1.0),
                                    entry_direction=direction,
                                    min_cross_disp=0.03, debounce_seconds=3.0))


def test_tripwire_counts_entry_left():
    tw = _tw("left")
    tw.update(1, (0.7, 0.5), 0.0)        # start right (outside)
    c = tw.update(1, (0.3, 0.5), 1.0)    # cross left (inside) => entry
    assert c is not None and c.direction == "entry"
    assert tw.entries == 1 and tw.exits == 0


def test_tripwire_counts_exit_and_debounces_reentry():
    tw = _tw("left")
    tw.update(1, (0.3, 0.5), 0.0)
    c = tw.update(1, (0.7, 0.5), 1.0)    # cross right => exit
    assert c.direction == "exit"
    # immediate same-direction recross within debounce is suppressed
    tw.update(1, (0.3, 0.5), 1.2)
    c2 = tw.update(1, (0.71, 0.5), 1.5)  # tries to exit again too soon
    assert c2 is None
    assert tw.exits == 1


def test_tripwire_ignores_jitter():
    tw = _tw("left")
    tw.update(1, (0.49, 0.5), 0.0)
    c = tw.update(1, (0.51, 0.5), 0.1)   # crosses but disp < min_cross_disp
    assert c is None


# ---------------- zones ----------------
def test_point_in_polygon_square():
    sq = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon((0.5, 0.5), sq)
    assert not point_in_polygon((1.5, 0.5), sq)


def test_zone_dwell_accumulates():
    z = Zone("aisle", "browse", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    zt = ZonePresenceTracker([z])
    zt.update(1, (0.5, 0.5), 100.0)
    zt.update(1, (0.5, 0.5), 105.0)
    rec = zt.all_records()[0]
    assert rec.zone == "aisle"
    assert rec.dwell_s == pytest.approx(5.0)
    assert rec.frames == 2
