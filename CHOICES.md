# CHOICES.md — Trade-offs & Engineering Decisions

This file is the "why". Each decision lists the trade-off taken and what was
given up, because in a system like this the judgment matters more than the code.

---

## 1. Two detector backends, with graceful degradation

**Decision.** A `Detector` interface with two implementations: **YOLOv8 +
ByteTrack** (production default) and an OpenCV **MOG2 motion** backend
(dependency-free fallback). `backend: auto` uses YOLO if weights are present,
otherwise motion.

**Why.** YOLO gives the best detection/tracking, but it needs torch + model
weights (~6 MB download, ideally a GPU). An evaluation box may have none of that,
or no network. Rather than fail the acceptance gate, the system *degrades*: the
motion backend needs nothing but OpenCV and still produces real, input-varying
counts — it is genuinely good at the one job that matters most, counting people
across the entrance line.

**Trade-off.** The motion backend can't tell a *stationary* shopper from the
background (MOG2 fades them out), and it fragments one person into several short
tracks. So under the motion backend, **browse "engaged" counts are upper bounds**,
not exact. We mitigate with a min-presence-frames filter and a dwell guard, and
the funnel's monotonic render keeps the displayed numbers sane. The entrance
count — the load-bearing metric — is unaffected because people *move* through a
door. **The bundled `data/events.db` was produced by the motion backend on the
real clips; switch to YOLO for production-grade zone counts.**

## 2. No face recognition / no cross-camera re-identification

**Decision.** Tracks are anonymous, ephemeral, and **per-camera** (`CAM_3#36`).
We never compute face embeddings or link a person across cameras.

**Why.** (a) Privacy — footfall analytics don't need identity, and storing
biometrics would be a liability and ethically wrong for a retail deployment.
(b) Honesty — without ReID we *cannot* truthfully claim to follow one shopper
from door → aisle → till, so we don't pretend to.

**Trade-off.** The funnel stages are *independent* per-window measurements rather
than a tracked individual journey. We're explicit about this: stages are rendered
monotonic for interpretability, and the headline conversion uses the two metrics
we *can* trust end-to-end (entrance count, POS invoices). A future privacy-safe
upgrade is anonymised body-appearance ReID with short-lived embeddings.

## 3. Conversion is windowed; the day figure is a labelled estimate

**Decision.** Conversion is computed over a configurable window. The provided
clips cover only ~3 minutes (~20:09–20:12), so we also publish a **projected day
conversion**: measured entry-rate × trading minutes vs the real 24 invoices.

**Why.** It would be dishonest to multiply the clip's footfall into a "day"
number and present it as measured. So everything day-level is tagged `EST` in the
API (`is_estimate: true`) and the dashboard, while window-level numbers are real
measurements. On the supplied data this yields ≈ **1.7 entries/min → ~1,100
projected day footfall → ~2.2% projected conversion** against 24 billed invoices.

**Trade-off.** A single 3-minute slice is a weak basis for a day projection;
real deployment ingests the full day and the estimate disappears. We keep it
because it makes the funnel meaningful on the sample and shows the *method*.

## 4. Edge cases handled

- **Re-entry / loitering on the line.** The tripwire debounces *any* re-crossing
  by the same track within a window (per-track), so a shopper hovering in the
  doorway is counted once. A genuine re-visit after the quiet window counts again.
- **Group arrivals.** Entries clustered tightly in time are flagged as a group
  (`/funnel.groups`); each person still counts toward footfall.
- **Staff.** Anyone in a `staff_only` zone (backroom CAM_4, behind-till on CAM_5)
  is classed `staff` and excluded from the customer funnel. Very long browse
  dwell is also treated as staff. POS gives a ground-truth staff list (5
  salespersons) used as a sanity check.
- **Occlusion / jitter.** The tracker tolerates `max_age` missed frames; the
  tripwire ignores sub-threshold displacements so shelf-edge jitter near the line
  doesn't generate phantom crossings.
- **Camera clock skew.** Each camera's `start_time` (read from its OSD) aligns
  feeds onto one wall-clock; production would use NTP-synced RTSP timestamps.

## 5. Storage: SQLite, append-only

**Decision.** Events go to a file-based SQLite log; analytics run on read.

**Why.** Zero-ops, ships in the container, gives real SQL for the queries, and
keeps ingestion (write) cleanly separated from analytics (read) so the same DB
can be re-queried over any window without recomputation. For multi-store /
high-throughput we'd move to TimescaleDB/ClickHouse behind the same `EventStore`
interface — the seam is already there.

## 6. Anomalies are rules, not a model

**Decision.** Threshold rules (queue build-up, long-dwell-no-purchase, footfall
spike z-score, idle entrance, low conversion), each with severity + the evidence
that fired it.

**Why.** In retail ops, an alert a manager can act on and trust beats a
higher-AUC black box they can't interpret. Every alert says exactly *why*. The
thresholds live in config; swapping in a learned detector later is a drop-in.

## 7. Performance choices

Processing runs at a throttled **5 fps** on **down-scaled** frames (you don't
need 30 fps/1080p to count people through a door). This cut per-camera processing
from minutes to ~40–60 s on a single CPU core while leaving counts unchanged.

## 8. Known limitations (stated plainly)

- Motion-backend zone counts are upper bounds (see §1); use YOLO for accuracy.
- The tripwire is calibrated to *these* camera angles; a new store needs zone/
  tripwire re-calibration in the YAML.
- Day-level conversion is an estimate from a short clip (see §3).
- No cross-camera identity, so the funnel is an aggregate, not a per-person path
  (see §2).
