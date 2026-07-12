"""End-to-end API tests with a seeded SQLite DB (no video needed)."""
import importlib
import os

from fastapi.testclient import TestClient


def _client(tmp_path, seeded_events):
    from store_intel.store.db import EventStore
    db_path = str(tmp_path / "api.db")
    store = EventStore(db_path)
    store.insert_events(seeded_events)
    store.record_run("motion", ["CAM_1", "CAM_3", "CAM_5"], len(seeded_events))

    # Point the app at our fixture DB + a small POS csv, then (re)import app.
    pos = tmp_path / "pos.csv"
    pos.write_text("invoice_number,order_date,order_time,qty,GMV,salesperson_name\n"
                   "INV1,10-04-2026,20:00:35,1,500,Asha\n")
    os.environ["EVENTS_DB"] = db_path
    os.environ["POS_CSV"] = str(pos)

    import store_intel.api.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.app)


def test_health(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    r = c.get("/health")
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "ok"
    assert j["entries"] == 4
    assert j["detector_backend"] == "motion"


def test_funnel_endpoint(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    j = c.get("/funnel").json()
    vals = [s["value"] for s in j["stages"]]
    assert vals == sorted(vals, reverse=True)
    assert j["footfall"] == 4
    assert j["purchases"] == 1               # INV1 falls in window
    assert j["conversion_pct"] == 25.0


def test_metrics_endpoint(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    j = c.get("/metrics").json()
    assert j["footfall"]["entries"] == 4
    assert j["day_projection"]["is_estimate"] is True


def test_events_filter(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    j = c.get("/events?event_type=entry").json()
    assert j["count"] == 4
    assert all(e["event_type"] == "entry" for e in j["events"])


def test_anomalies_endpoint(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    j = c.get("/anomalies").json()
    assert "anomalies" in j and isinstance(j["anomalies"], list)


def test_cameras_endpoint(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    j = c.get("/cameras").json()
    assert len(j["cameras"]) == 5
    assert any(cam["has_tripwire"] for cam in j["cameras"])


def test_dashboard_served(tmp_path, seeded_events):
    c = _client(tmp_path, seeded_events)
    r = c.get("/")
    assert r.status_code == 200
    assert "Store Intelligence" in r.text


def test_window_scoping_changes_results(tmp_path, seeded_events):
    """Outputs must vary with the query window (integrity / no-hardcoding)."""
    c = _client(tmp_path, seeded_events)
    full = c.get("/funnel").json()
    narrow = c.get("/funnel?start=2026-04-10T20:00:30&end=2026-04-10T20:00:50").json()
    assert narrow["footfall"] != full["footfall"]
