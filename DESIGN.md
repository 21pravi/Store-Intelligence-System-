# DESIGN.md — Store Intelligence System

A pipeline that turns raw store CCTV into business metrics — footfall, a
conversion funnel, dwell/engagement, and anomalies — exposed through a JSON API
and a live dashboard. Built for **Purplle, Brigade Road, Bangalore** (store
`ST1008`) from five camera feeds, the day's POS export, and the floor plan.

---

## 1. The problem, decomposed

The headline business metric is **conversion rate = purchases ÷ footfall**.
Neither term is handed to us directly:

- *Footfall* must be derived from the **entrance camera** by counting people who
  actually cross the threshold (not loiterers, not the same person twice).
- *Purchases* come from the **POS export** (distinct invoices, not line items).
- Everything between — who browsed, who reached the till, how long they dwelled —
  comes from the **aisle and checkout cameras** and forms the funnel that
  explains *where* shoppers drop off.

So the system is fundamentally a **sensor-fusion** problem: five independent
video sensors + one transactional sensor, reconciled on a shared clock into one
coherent story of a shopping session.

## 2. Camera roles (derived from the footage + floor plan)

| Camera | Role        | What it watches                              | Produces                  |
|--------|-------------|----------------------------------------------|---------------------------|
| CAM_3  | `entrance`  | Door / threshold                             | `entry` / `exit` (footfall) |
| CAM_1  | `browse`    | Left wall — skincare (Face Shop, DermaCo…)   | `zone_presence` + dwell   |
| CAM_2  | `browse`    | Right wall — makeup (Lakmé, Faces Canada…)   | `zone_presence` + dwell   |
| CAM_5  | `checkout`  | Cash counter                                 | `checkout_presence`, staff |
| CAM_4  | `backroom`  | Stockroom (staff-only)                       | `staff_presence`          |

These roles are **config, not code** (`config/store_config.yaml`): zones and the
entrance tripwire are normalised polygons/lines, so re-deploying to another store
is a config change, not a rewrite.

## 3. Architecture & data flow

```
 video ─► Detector ─► Tracker ─► Pipeline logic ─► Events ─► SQLite ─► Analytics ─► API ─► Dashboard
          (YOLO|       (IDs)     tripwire/zones    (schema)            funnel/        (FastAPI)
           motion)                                                     metrics/
                                                                       anomaly
                                            POS CSV ───────────────────────┘
```

The seam that holds it together is the **event**. Every camera, regardless of
detector backend, emits the *same* flat event (`schema.py`). Downstream code
only ever reads events + POS rows — it never touches pixels. That makes the
analytics pure, reproducible, and unit-testable with synthetic events.

### Layers
- **`detectors/`** — `Detector` protocol with two backends. `YoloDetector`
  (YOLOv8 + ByteTrack, production) and `MotionDetector` (MOG2 background
  subtraction, zero-dependency fallback). A factory resolves `auto`/`yolo`/`motion`.
- **`tracking/`** — `CentroidTracker`, a SORT-lite (greedy IoU + centroid
  association, max-age for occlusion) used when the backend doesn't supply IDs.
- **`pipeline/`** — `TripwireCounter` (directional line crossing, debounce,
  re-entry), `ZonePresenceTracker` (point-in-polygon dwell), `CameraWorker`
  (frame → events), `runner` (all cameras → store).
- **`fusion/`** — `pos.py` (line items → invoices), `sessions.py` (events + POS →
  funnel).
- **`analytics/`** — `metrics.py` (footfall, dwell, occupancy, revenue, day
  projection, time series) and `anomaly.py` (explainable rules).
- **`store/`** — append-only SQLite event log + heatmaps + run provenance.
- **`api/`** — FastAPI endpoints + the dashboard SPA.

## 4. Event schema

One event = one observation about one anonymous track at one instant:

```jsonc
{
  "event_id": "…", "ts": "2026-04-10T20:10:39.8",
  "camera_id": "CAM_3", "zone": "entrance",
  "event_type": "entry|exit|zone_presence|checkout_presence|staff_presence",
  "track_id": "CAM_3#36",          // ephemeral, per-camera; NOT an identity
  "person_class": "customer|staff|unknown",
  "confidence": 0.55, "bbox": {…}, "dwell_s": 12.0, "meta": {…}
}
```

`ts` is wall-clock (camera `start_time` from the on-screen display + frame
offset), so events from different cameras share a timeline and can be fused.
Coordinates are normalised (0–1) — resolution-independent.

## 5. The funnel model (and why it's honest)

Cameras are **not** identity-linked across views (no face/ReID — see
`CHOICES.md`). We therefore measure each stage *independently* within a window as
a count of distinct anonymous tracks, then render a **monotonic** funnel (a later
stage can never exceed an earlier one):

```
 entered  ──►  engaged  ──►  reached_till  ──►  purchased
 (CAM_3)       (CAM_1/2)     (CAM_5)            (POS)
```

The headline **conversion = purchased ÷ footfall** is computed directly and
honestly. Stage drop-off is reported between consecutive stages.

## 6. API surface

| Endpoint     | Purpose                                                        |
|--------------|----------------------------------------------------------------|
| `GET /health`| Liveness + provenance (backend, event count, last ingest)      |
| `GET /metrics`| Footfall, engagement, dwell, revenue, day projection, series  |
| `GET /funnel`| Conversion funnel + drop-off + group arrivals                  |
| `GET /events`| Raw structured events (filter by type/camera/window)           |
| `GET /anomalies`| Detected anomalies with severity + evidence                 |
| `GET /cameras`| Camera roster, roles, zones, presence heatmaps                |
| `GET /`      | Live dashboard (single page, polls the API)                    |

All analytics endpoints accept `?start=&end=` to scope the window — which is
exactly how we demonstrate that outputs *vary with input* (a wider window picks
up more POS transactions and changes conversion).

## 7. Observability & provenance

Structured logs per camera (`frames / entries / exits / events`). A `runs` table
records the detector backend, cameras, event count and timestamp of every
ingestion, surfaced at `/health` so a reviewer can confirm the numbers came from
a real computation, not a fixture.
