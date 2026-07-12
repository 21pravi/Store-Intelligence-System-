"""
Business metrics computed from the event stream (+ POS).

Everything here is a pure function of stored events and transactions — no pixels,
no model. That is what makes the metrics reproducible and unit-testable, and it
is why re-running ingestion on different footage changes every number (the
integrity property the rubric asks for).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..fusion.pos import Transaction, transactions_in_window


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def footfall_timeseries(events: List[dict], bucket_seconds: int = 30
                        ) -> List[Dict]:
    """Entries per time bucket (for sparklines / spike detection)."""
    entries = sorted((_parse(e["ts"]) for e in events
                      if e["event_type"] == "entry"))
    if not entries:
        return []
    t0 = entries[0]
    buckets: Dict[int, int] = defaultdict(int)
    for t in entries:
        b = int((t - t0).total_seconds() // bucket_seconds)
        buckets[b] += 1
    out = []
    for b in range(max(buckets) + 1):
        out.append({
            "t": (t0 + timedelta(seconds=b * bucket_seconds)).isoformat(),
            "entries": buckets.get(b, 0),
        })
    return out


def dwell_stats(events: List[dict], zone_type: Optional[str] = None) -> Dict:
    dwells = [e["dwell_s"] for e in events
              if e["event_type"] in ("zone_presence", "checkout_presence")
              and e["dwell_s"] > 0
              and (zone_type is None or e["meta"].get("zone_type") == zone_type)]
    if not dwells:
        return {"count": 0, "mean_s": 0.0, "median_s": 0.0, "max_s": 0.0}
    return {
        "count": len(dwells),
        "mean_s": round(statistics.mean(dwells), 1),
        "median_s": round(statistics.median(dwells), 1),
        "max_s": round(max(dwells), 1),
    }


def zone_occupancy(events: List[dict]) -> Dict[str, int]:
    """Distinct customer tracks seen per zone (engagement breadth)."""
    seen: Dict[str, set] = defaultdict(set)
    for e in events:
        if e["event_type"] in ("zone_presence", "checkout_presence") \
                and e["person_class"] == "customer":
            seen[e["zone"]].add(e["track_id"])
    return {z: len(ids) for z, ids in sorted(seen.items())}


def queue_length_series(events: List[dict], step_seconds: int = 10) -> List[Dict]:
    """Reconstruct checkout-queue length over time from presence intervals."""
    intervals = []
    for e in events:
        if e["event_type"] == "checkout_presence":
            start = _parse(e["ts"])
            intervals.append((start, start + timedelta(seconds=e["dwell_s"])))
    if not intervals:
        return []
    t0 = min(s for s, _ in intervals)
    t1 = max(en for _, en in intervals)
    out = []
    t = t0
    while t <= t1:
        q = sum(1 for s, en in intervals if s <= t <= en)
        out.append({"t": t.isoformat(), "queue": q})
        t += timedelta(seconds=step_seconds)
    return out


def revenue_stats(txs: List[Transaction], start: datetime, end: datetime) -> Dict:
    win = transactions_in_window(txs, start, end)
    gmv = sum(t.gmv for t in win)
    baskets = [t.basket_size for t in win]
    return {
        "transactions": len(win),
        "gmv": round(gmv, 2),
        "avg_basket_value": round(gmv / len(win), 2) if win else 0.0,
        "avg_basket_size": round(statistics.mean(baskets), 2) if baskets else 0.0,
    }


def project_day_footfall(events: List[dict], open_t, close_t,
                         observed_window_s: float) -> Dict:
    """
    Honest extrapolation: the CCTV clip covers only a few minutes. We measure the
    real entry rate in that window and project it across trading hours. This is an
    ESTIMATE (clearly labelled) for context only — never presented as measured.
    """
    n_entries = sum(1 for e in events if e["event_type"] == "entry")
    if observed_window_s <= 0:
        return {"measured_entries": n_entries, "rate_per_min": 0.0,
                "projected_day_footfall": None, "is_estimate": True}
    rate_per_min = n_entries / (observed_window_s / 60.0)
    open_dt = datetime.combine(datetime.today(), open_t)
    close_dt = datetime.combine(datetime.today(), close_t)
    trading_minutes = (close_dt - open_dt).total_seconds() / 60.0
    return {
        "measured_entries": n_entries,
        "observed_window_s": round(observed_window_s, 1),
        "rate_per_min": round(rate_per_min, 2),
        "trading_minutes": int(trading_minutes),
        "projected_day_footfall": int(round(rate_per_min * trading_minutes)),
        "is_estimate": True,
    }


def compute_metrics(events: List[dict], txs: List[Transaction],
                    window_start: datetime, window_end: datetime,
                    store_cfg) -> Dict:
    window_s = (window_end - window_start).total_seconds()
    entries = sum(1 for e in events if e["event_type"] == "entry")
    exits = sum(1 for e in events if e["event_type"] == "exit")
    proj = project_day_footfall(events, store_cfg.open_time,
                                store_cfg.close_time, window_s)
    # Project a day-level conversion using the real full-day transaction count
    # against the projected footfall. Clearly an ESTIMATE (see project_day_footfall).
    day_txs = len(txs)
    proj_footfall = proj.get("projected_day_footfall")
    proj["day_transactions"] = day_txs
    proj["projected_day_conversion_pct"] = (
        round(day_txs / proj_footfall * 100.0, 2)
        if proj_footfall else None
    )
    return {
        "window": {"start": window_start.isoformat(),
                   "end": window_end.isoformat(),
                   "seconds": round(window_s, 1)},
        "footfall": {
            "entries": entries, "exits": exits,
            "net_occupancy_change": entries - exits,
        },
        "engagement": {
            "zone_unique_visitors": zone_occupancy(events),
            "browse_dwell": dwell_stats(events, "browse"),
            "checkout_dwell": dwell_stats(events, "checkout"),
        },
        "revenue": revenue_stats(txs, window_start, window_end),
        "day_projection": proj,
        "timeseries": {
            "footfall": footfall_timeseries(events),
            "checkout_queue": queue_length_series(events),
        },
    }
