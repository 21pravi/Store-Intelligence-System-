"""
Store Intelligence API (FastAPI).

Endpoints
---------
GET /health      liveness + provenance (detector backend, event count, last run)
GET /metrics     footfall, engagement, dwell, revenue, day projection, timeseries
GET /funnel      conversion funnel (entered -> engaged -> till -> purchased)
GET /events      raw structured events (filterable) — proves real event generation
GET /anomalies   detected anomalies with severity + evidence
GET /cameras      camera roster, roles, zones, presence heatmaps
GET /             live dashboard (single-page app)

All analytics endpoints accept optional ?start=&end= ISO timestamps to scope the
window; they default to the full ingested time span.
"""
from __future__ import annotations

import os
from datetime import datetime
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from ..analytics.anomaly import detect_anomalies
from ..analytics.metrics import compute_metrics
from ..config import load_config
from ..fusion.pos import load_transactions, staff_from_pos
from ..fusion.sessions import build_funnel, detect_groups
from ..store.db import EventStore

DB_PATH = os.environ.get("EVENTS_DB", "./data/events.db")
POS_CSV = os.environ.get("POS_CSV", "./data/pos.csv")
DASHBOARD = os.path.join(os.path.dirname(__file__), "dashboard.html")

app = FastAPI(title="Store Intelligence API", version="1.0.0",
              description="CCTV-driven retail footfall, funnel & anomaly intelligence.")


@lru_cache(maxsize=1)
def _cfg():
    return load_config()


def _store() -> EventStore:
    return EventStore(DB_PATH)


@lru_cache(maxsize=1)
def _txs():
    if os.path.exists(POS_CSV):
        return load_transactions(POS_CSV)
    return []


def _window(store: EventStore, start: Optional[str], end: Optional[str]):
    a, b = store.time_bounds()
    if a is None or b is None:
        raise HTTPException(404, "No events ingested yet. Run scripts/ingest.py first.")
    if start:
        a = datetime.fromisoformat(start)
    if end:
        b = datetime.fromisoformat(end)
    return a, b


@app.get("/health")
def health():
    store = _store()
    run = store.last_run()
    return {
        "status": "ok",
        "events": store.count(),
        "entries": store.count("entry"),
        "exits": store.count("exit"),
        "detector_backend": (run or {}).get("backend"),
        "last_ingest": (run or {}).get("created"),
        "pos_loaded": len(_txs()),
        "store": _cfg().store.name,
    }


@app.get("/metrics")
def metrics(start: Optional[str] = None, end: Optional[str] = None):
    store = _store()
    a, b = _window(store, start, end)
    ev = store.query_events(start=a, end=b, limit=100000)
    m = compute_metrics(ev, _txs(), a, b, _cfg().store)
    return JSONResponse(m)


@app.get("/funnel")
def funnel(start: Optional[str] = None, end: Optional[str] = None):
    store = _store()
    a, b = _window(store, start, end)
    ev = store.query_events(start=a, end=b, limit=100000)
    fr = build_funnel(ev, _txs(), a, b, _cfg().funnel)
    out = fr.as_dict()
    groups = detect_groups([e for e in ev if e["event_type"] == "entry"])
    out["groups"] = {"count": len(groups),
                     "sizes": [len(g) for g in groups],
                     "grouped_arrivals": sum(1 for g in groups if len(g) > 1)}
    return JSONResponse(out)


@app.get("/events")
def events(event_type: Optional[str] = None, camera_id: Optional[str] = None,
           start: Optional[str] = None, end: Optional[str] = None,
           limit: int = Query(200, le=5000)):
    store = _store()
    s = datetime.fromisoformat(start) if start else None
    e = datetime.fromisoformat(end) if end else None
    rows = store.query_events(event_type=event_type, camera_id=camera_id,
                              start=s, end=e, limit=limit)
    return {"count": len(rows), "events": rows}


@app.get("/anomalies")
def anomalies(start: Optional[str] = None, end: Optional[str] = None):
    store = _store()
    a, b = _window(store, start, end)
    ev = store.query_events(start=a, end=b, limit=100000)
    m = compute_metrics(ev, _txs(), a, b, _cfg().store)
    conv = m["day_projection"].get("projected_day_conversion_pct")
    found = detect_anomalies(ev, _cfg().anomaly, conv)
    return {"count": len(found), "anomalies": found}


@app.get("/cameras")
def cameras():
    cfg = _cfg()
    store = _store()
    out = []
    for c in cfg.cameras:
        out.append({
            "id": c.id, "role": c.role, "zone": c.zone,
            "zones": [{"name": z.name, "type": z.type} for z in c.zones],
            "has_tripwire": c.tripwire is not None,
            "heatmap": store.heatmap(c.id),
        })
    return {"store": cfg.store.name, "expected_staff": cfg.store.expected_staff,
            "pos_staff": staff_from_pos(_txs()), "cameras": out}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    if os.path.exists(DASHBOARD):
        with open(DASHBOARD, "r") as fh:
            return HTMLResponse(fh.read())
    return HTMLResponse("<h1>Store Intelligence</h1><p>Dashboard file missing.</p>")
