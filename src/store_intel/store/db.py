"""
SQLite event store.

Why SQLite: zero-ops, file-based, ships inside the container, and gives us real
SQL for the analytics queries — no hand-rolled in-memory filtering. The event
table is an append-only log; analytics are computed on read. A tiny `runs` table
records provenance (detector backend, when ingested) so the API can prove the
numbers came from real computation, not a fixture.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from ..schema import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    ts           TEXT NOT NULL,
    camera_id    TEXT NOT NULL,
    zone         TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    track_id     TEXT NOT NULL,
    person_class TEXT NOT NULL,
    confidence   REAL NOT NULL,
    bbox_x REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    dwell_s      REAL NOT NULL DEFAULT 0,
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_events_ts   ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS ix_events_cam  ON events(camera_id);

CREATE TABLE IF NOT EXISTS heatmaps (
    camera_id TEXT PRIMARY KEY,
    rows INTEGER, cols INTEGER,
    data TEXT NOT NULL              -- JSON 2D array, normalised 0..1
);

CREATE TABLE IF NOT EXISTS runs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    created   TEXT NOT NULL,
    backend   TEXT NOT NULL,
    cameras   TEXT NOT NULL,
    n_events  INTEGER NOT NULL,
    notes     TEXT
);
"""


class EventStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- writes -----------------------------------------------------------
    def reset(self) -> None:
        with self._conn() as c:
            c.executescript(
                "DELETE FROM events; DELETE FROM heatmaps; DELETE FROM runs;")

    def insert_events(self, events: Iterable[Event]) -> int:
        rows = [e.to_row() for e in events]
        with self._conn() as c:
            c.executemany(
                """INSERT OR REPLACE INTO events
                   (event_id, ts, camera_id, zone, event_type, track_id,
                    person_class, confidence, bbox_x, bbox_y, bbox_w, bbox_h,
                    dwell_s, meta)
                   VALUES (:event_id,:ts,:camera_id,:zone,:event_type,:track_id,
                    :person_class,:confidence,:bbox_x,:bbox_y,:bbox_w,:bbox_h,
                    :dwell_s, :meta_json)""",
                [{**r, "meta_json": json.dumps(r["meta"])} for r in rows],
            )
        return len(rows)

    def save_heatmap(self, camera_id: str, matrix) -> None:
        import numpy as np
        m = np.asarray(matrix, dtype=float)
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO heatmaps(camera_id,rows,cols,data) VALUES(?,?,?,?)",
                (camera_id, m.shape[0], m.shape[1],
                 json.dumps([[round(float(v), 4) for v in row] for row in m])),
            )

    def record_run(self, backend: str, cameras: List[str], n_events: int,
                   notes: str = "") -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO runs(created,backend,cameras,n_events,notes) VALUES(?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), backend, ",".join(cameras),
                 n_events, notes),
            )

    # ---- reads ------------------------------------------------------------
    def query_events(self, event_type: Optional[str] = None,
                     camera_id: Optional[str] = None,
                     start: Optional[datetime] = None,
                     end: Optional[datetime] = None,
                     limit: int = 1000) -> List[dict]:
        q = "SELECT * FROM events WHERE 1=1"
        args: list = []
        if event_type:
            q += " AND event_type=?"; args.append(event_type)
        if camera_id:
            q += " AND camera_id=?"; args.append(camera_id)
        if start:
            q += " AND ts>=?"; args.append(start.isoformat())
        if end:
            q += " AND ts<=?"; args.append(end.isoformat())
        q += " ORDER BY ts LIMIT ?"; args.append(limit)
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(q, args).fetchall()]
        for r in rows:
            r["meta"] = json.loads(r["meta"])
        return rows

    def count(self, event_type: Optional[str] = None) -> int:
        q = "SELECT COUNT(*) n FROM events"
        args: list = []
        if event_type:
            q += " WHERE event_type=?"; args.append(event_type)
        with self._conn() as c:
            return int(c.execute(q, args).fetchone()["n"])

    def time_bounds(self):
        with self._conn() as c:
            r = c.execute("SELECT MIN(ts) a, MAX(ts) b FROM events").fetchone()
        a = datetime.fromisoformat(r["a"]) if r["a"] else None
        b = datetime.fromisoformat(r["b"]) if r["b"] else None
        return a, b

    def heatmap(self, camera_id: str) -> Optional[List[List[float]]]:
        with self._conn() as c:
            r = c.execute("SELECT data FROM heatmaps WHERE camera_id=?",
                          (camera_id,)).fetchone()
        return json.loads(r["data"]) if r else None

    def last_run(self) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(r) if r else None
