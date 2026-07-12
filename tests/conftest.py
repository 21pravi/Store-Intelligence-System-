import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from store_intel.schema import BBox, Event, EventType, PersonClass  # noqa: E402
from store_intel.store.db import EventStore  # noqa: E402

BASE = datetime(2026, 4, 10, 20, 0, 0)


def mk_event(t_off, etype, cam, zone, tid, pclass=PersonClass.CUSTOMER, dwell=0.0,
             meta=None):
    return Event(
        ts=BASE + timedelta(seconds=t_off), camera_id=cam, zone=zone,
        event_type=etype, track_id=tid, person_class=pclass, dwell_s=dwell,
        bbox=BBox(x=0.4, y=0.4, w=0.1, h=0.2), meta=meta or {},
    )


@pytest.fixture
def seeded_events():
    """A small, deterministic funnel: 4 entries, browse, till, used by analytics."""
    evs = [
        mk_event(0, EventType.ENTRY, "CAM_3", "entrance", "CAM_3#1", PersonClass.UNKNOWN),
        mk_event(2, EventType.ENTRY, "CAM_3", "entrance", "CAM_3#2", PersonClass.UNKNOWN),
        mk_event(40, EventType.ENTRY, "CAM_3", "entrance", "CAM_3#3", PersonClass.UNKNOWN),
        mk_event(42, EventType.ENTRY, "CAM_3", "entrance", "CAM_3#4", PersonClass.UNKNOWN),
        mk_event(5, EventType.EXIT, "CAM_3", "entrance", "CAM_3#9", PersonClass.UNKNOWN),
        # browse
        mk_event(10, EventType.ZONE_PRESENCE, "CAM_1", "skincare_aisle", "CAM_1#1",
                 PersonClass.CUSTOMER, dwell=12.0, meta={"zone_type": "browse"}),
        mk_event(15, EventType.ZONE_PRESENCE, "CAM_2", "makeup_aisle", "CAM_2#1",
                 PersonClass.CUSTOMER, dwell=8.0, meta={"zone_type": "browse"}),
        mk_event(16, EventType.ZONE_PRESENCE, "CAM_2", "makeup_aisle", "CAM_2#9",
                 PersonClass.CUSTOMER, dwell=0.5, meta={"zone_type": "browse"}),  # too short
        # till
        mk_event(30, EventType.CHECKOUT_PRESENCE, "CAM_5", "checkout_queue", "CAM_5#1",
                 PersonClass.CUSTOMER, dwell=20.0, meta={"zone_type": "checkout"}),
        # staff
        mk_event(12, EventType.STAFF_PRESENCE, "CAM_4", "backroom", "CAM_4#1",
                 PersonClass.STAFF, dwell=60.0, meta={"zone_type": "staff_only"}),
    ]
    return evs


@pytest.fixture
def temp_store(tmp_path, seeded_events):
    db = EventStore(str(tmp_path / "t.db"))
    db.insert_events(seeded_events)
    db.record_run("motion", ["CAM_1", "CAM_3"], len(seeded_events))
    return db
