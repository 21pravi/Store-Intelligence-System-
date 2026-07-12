from datetime import datetime, timedelta

from store_intel.analytics.anomaly import detect_anomalies
from store_intel.analytics.metrics import (compute_metrics, footfall_timeseries,
                                           queue_length_series)
from store_intel.config import StoreConfig
from store_intel.fusion.pos import Transaction
from store_intel.fusion.sessions import build_funnel, detect_groups

BASE = datetime(2026, 4, 10, 20, 0, 0)
FUNNEL_CFG = {"stages": [
    {"key": "entered", "label": "Entered store"},
    {"key": "engaged", "label": "Browsed"},
    {"key": "reached_till", "label": "Reached till"},
    {"key": "purchased", "label": "Purchased"},
]}
STORE = StoreConfig("ST1008", "Brigade_Bangalore", "Bangalore", "Asia/Kolkata",
                    datetime(2026, 4, 10, 11).time(), datetime(2026, 4, 10, 22).time(), 6)


def _rows(store):
    return store.query_events(limit=1000)


# ---------------- POS ----------------
def test_pos_collapses_line_items_to_invoices():
    from store_intel.fusion.pos import load_transactions
    import csv
    import tempfile
    import os
    rows = [
        "invoice_number,order_date,order_time,qty,GMV,salesperson_name",
        "INV1,10-04-2026,20:00:10,1,100,Asha",
        "INV1,10-04-2026,20:00:10,2,200,Asha",   # same invoice -> merged
        "INV2,10-04-2026,20:05:00,1,50,Ravi",
    ]
    fd, path = tempfile.mkstemp(suffix=".csv")
    os.write(fd, ("\n".join(rows)).encode()); os.close(fd)
    txs = load_transactions(path)
    os.unlink(path)
    assert len(txs) == 2
    inv1 = [t for t in txs if t.invoice_number == "INV1"][0]
    assert inv1.items == 3 and inv1.gmv == 300


# ---------------- funnel ----------------
def test_funnel_monotonic_and_conversion(temp_store):
    ev = _rows(temp_store)
    txs = [Transaction("INV1", BASE + timedelta(seconds=35), "Asha", 2, 500)]
    fr = build_funnel(ev, txs, BASE, BASE + timedelta(seconds=120), FUNNEL_CFG)
    vals = [s.value for s in fr.stages]
    assert vals == sorted(vals, reverse=True)          # monotonic non-increasing
    assert fr.footfall == 4                            # 4 entries seeded
    assert fr.purchases == 1
    assert fr.conversion_pct == 25.0                   # 1/4


def test_funnel_engaged_dwell_guard(temp_store):
    # The 0.5s browse track must NOT count as engaged (min_dwell 2s guard).
    ev = _rows(temp_store)
    fr = build_funnel(ev, [], BASE, BASE + timedelta(seconds=120), FUNNEL_CFG)
    engaged = [s for s in fr.stages if s.key == "engaged"][0]
    assert engaged.observed == 2   # CAM_1#1 and CAM_2#1, not CAM_2#9


def test_detect_groups_clusters_close_arrivals(temp_store):
    ev = [e for e in _rows(temp_store) if e["event_type"] == "entry"]
    groups = detect_groups(ev, window_s=5.0)
    sizes = sorted(len(g) for g in groups)
    # entries at t=0,2 (group) and t=40,42 (group) -> two groups of 2
    assert sizes == [2, 2]


# ---------------- metrics ----------------
def test_footfall_timeseries_buckets(temp_store):
    ev = _rows(temp_store)
    ts = footfall_timeseries(ev, bucket_seconds=30)
    total = sum(p["entries"] for p in ts)
    assert total == 4


def test_compute_metrics_shape(temp_store):
    ev = _rows(temp_store)
    m = compute_metrics(ev, [], BASE, BASE + timedelta(seconds=120), STORE)
    assert m["footfall"]["entries"] == 4
    assert m["footfall"]["exits"] == 1
    assert "skincare_aisle" in m["engagement"]["zone_unique_visitors"]
    assert m["day_projection"]["is_estimate"] is True
    assert m["day_projection"]["projected_day_footfall"] > 0


# ---------------- anomaly ----------------
def test_anomaly_low_conversion_fires():
    found = detect_anomalies([], {"conversion_floor_pct": 5.0}, conversion_pct=2.0)
    kinds = [a["kind"] for a in found]
    assert "low_conversion" in kinds


def test_anomaly_queue_buildup():
    # 5 overlapping checkout presences -> queue peak 5 -> warning
    ev = []
    for i in range(5):
        ev.append({"event_type": "checkout_presence", "person_class": "customer",
                   "ts": (BASE + timedelta(seconds=i)).isoformat(),
                   "dwell_s": 30.0, "zone": "checkout_queue", "track_id": f"q{i}",
                   "meta": {"zone_type": "checkout"}})
    found = detect_anomalies(ev, {"queue_length_warn": 4, "queue_length_crit": 6},
                             conversion_pct=50.0)
    assert any(a["kind"] == "checkout_queue" for a in found)


def test_queue_length_series_overlap():
    ev = [{"event_type": "checkout_presence", "ts": BASE.isoformat(), "dwell_s": 20.0},
          {"event_type": "checkout_presence",
           "ts": (BASE + timedelta(seconds=5)).isoformat(), "dwell_s": 20.0}]
    series = queue_length_series(ev, step_seconds=5)
    assert max(p["queue"] for p in series) == 2
