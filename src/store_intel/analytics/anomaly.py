"""
Anomaly detection.

A set of explainable, threshold-based rules over the event stream. Each anomaly
carries a severity, a human-readable message, and the evidence that triggered it,
so the dashboard can show *why* something fired (and a reviewer can trust it).
These are intentionally rules, not a black-box model: in a retail-ops setting an
alert that a manager can act on beats a higher-AUC score they can't interpret.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Dict, List

from .metrics import footfall_timeseries, queue_length_series


def _anom(kind: str, severity: str, message: str, evidence: Dict) -> Dict:
    return {"kind": kind, "severity": severity, "message": message,
            "evidence": evidence}


def detect_anomalies(events: List[dict], anomaly_cfg: dict,
                     conversion_pct: float, store_open: bool = True) -> List[Dict]:
    out: List[Dict] = []

    # 1) Checkout queue build-up.
    q = queue_length_series(events, step_seconds=5)
    if q:
        max_q = max(p["queue"] for p in q)
        warn = anomaly_cfg.get("queue_length_warn", 4)
        crit = anomaly_cfg.get("queue_length_crit", 6)
        if max_q >= crit:
            out.append(_anom("checkout_queue", "critical",
                             f"Severe queue build-up at till (peak {max_q}).",
                             {"peak_queue": max_q, "threshold": crit}))
        elif max_q >= warn:
            out.append(_anom("checkout_queue", "warning",
                             f"Queue building at till (peak {max_q}).",
                             {"peak_queue": max_q, "threshold": warn}))

    # 2) Long browse dwell with no purchase nearby (loss-prevention / lost sale).
    thr_min = anomaly_cfg.get("dwell_no_purchase_minutes", 8)
    for e in events:
        if e["event_type"] == "zone_presence" and e["person_class"] == "customer":
            if e["dwell_s"] >= thr_min * 60:
                out.append(_anom("long_dwell_no_purchase", "warning",
                                 f"Customer dwelled {e['dwell_s']/60:.1f} min in "
                                 f"{e['zone']} — assistance or loss-prevention check.",
                                 {"zone": e["zone"], "track_id": e["track_id"],
                                  "dwell_s": round(e["dwell_s"], 1)}))

    # 3) Footfall spike (z-score on entries-per-bucket).
    ts = footfall_timeseries(events, bucket_seconds=30)
    counts = [p["entries"] for p in ts]
    if len(counts) >= 4 and statistics.pstdev(counts) > 0:
        mu, sd = statistics.mean(counts), statistics.pstdev(counts)
        zthr = anomaly_cfg.get("footfall_spike_zscore", 2.5)
        for p in ts:
            z = (p["entries"] - mu) / sd
            if z >= zthr:
                out.append(_anom("footfall_spike", "info",
                                 f"Footfall spike: {p['entries']} entries in a "
                                 f"30s window (z={z:.1f}).",
                                 {"t": p["t"], "entries": p["entries"],
                                  "zscore": round(z, 2)}))

    # 4) Idle entrance while open.
    if store_open and ts:
        idle_min = anomaly_cfg.get("idle_entrance_minutes", 20)
        total_entries = sum(counts)
        span_min = len(counts) * 0.5
        if total_entries == 0 and span_min >= idle_min:
            out.append(_anom("idle_entrance", "warning",
                             f"No entries for {span_min:.0f} min during trading hours.",
                             {"span_minutes": span_min}))

    # 5) Sustained low conversion.
    floor = anomaly_cfg.get("conversion_floor_pct", 5.0)
    if conversion_pct is not None and 0 < conversion_pct < floor:
        out.append(_anom("low_conversion", "warning",
                         f"Conversion {conversion_pct:.1f}% is below floor {floor}%.",
                         {"conversion_pct": round(conversion_pct, 2),
                          "floor_pct": floor}))

    return out
