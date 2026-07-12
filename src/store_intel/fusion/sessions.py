"""
Funnel construction & session logic.

Cameras are NOT identity-linked across views (we deliberately run no face/ReID
model — see CHOICES.md on privacy). So we cannot follow one physical person from
door -> aisle -> till. Instead we measure each funnel stage independently within
a time window as a count of distinct anonymous tracks, then render a *monotonic*
funnel for interpretability (a later stage can never exceed an earlier one).
The headline conversion rate is computed directly and honestly as
purchased / footfall.

Also handles, at the entrance:
  * re-entry      : same track re-crossing inside the debounce window is already
                    suppressed upstream (tripwire). A crossing after the window is
                    a genuine new visit and is counted.
  * group arrival : ENTRY events clustered tightly in time are flagged as a group
                    (each person still counts toward footfall).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .pos import Transaction, transactions_in_window


@dataclass
class FunnelStage:
    key: str
    label: str
    observed: int       # raw distinct tracks/transactions measured at this stage
    value: int          # monotonic value used for the funnel display


@dataclass
class FunnelResult:
    window_start: datetime
    window_end: datetime
    stages: List[FunnelStage]
    footfall: int
    purchases: int
    conversion_pct: float
    drop_off: Dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "window": {"start": self.window_start.isoformat(),
                       "end": self.window_end.isoformat()},
            "footfall": self.footfall,
            "purchases": self.purchases,
            "conversion_pct": round(self.conversion_pct, 2),
            "stages": [
                {"key": s.key, "label": s.label,
                 "observed": s.observed, "value": s.value}
                for s in self.stages
            ],
            "drop_off_pct": {k: round(v, 2) for k, v in self.drop_off.items()},
        }


def _distinct_tracks(events: List[dict], types, customer_only=True,
                     min_dwell_s: float = 0.0) -> int:
    ids = set()
    types = set(types)
    for e in events:
        if e["event_type"] not in types:
            continue
        if customer_only and e["person_class"] == "staff":
            continue
        if e.get("dwell_s", 0.0) < min_dwell_s:
            continue
        ids.add(e["track_id"])
    return len(ids)


def detect_groups(entry_events: List[dict], window_s: float = 5.0) -> List[List[str]]:
    """Cluster ENTRY events that arrive within `window_s` of each other."""
    evs = sorted((e for e in entry_events if e["event_type"] == "entry"),
                 key=lambda e: e["ts"])
    groups: List[List[str]] = []
    cur: List[str] = []
    last_ts: Optional[datetime] = None
    for e in evs:
        ts = datetime.fromisoformat(e["ts"])
        if last_ts is None or (ts - last_ts).total_seconds() <= window_s:
            cur.append(e["track_id"])
        else:
            groups.append(cur)
            cur = [e["track_id"]]
        last_ts = ts
    if cur:
        groups.append(cur)
    return groups


def build_funnel(events: List[dict], transactions: List[Transaction],
                 window_start: datetime, window_end: datetime,
                 funnel_cfg: dict, staff_entries_est: int = 0) -> FunnelResult:
    # Stage 1: footfall (entrance entries, minus an estimate of staff crossings).
    footfall_raw = sum(1 for e in events if e["event_type"] == "entry")
    footfall = max(0, footfall_raw - staff_entries_est)

    # Stage 2: engaged — distinct customer tracks that genuinely lingered (>=2s)
    # in a browse zone. The dwell guard suppresses motion-backend track noise.
    engaged = _distinct_tracks(events, {"zone_presence"}, min_dwell_s=2.0)

    # Stage 3: reached till — distinct customer tracks at the checkout queue.
    reached = _distinct_tracks(events, {"checkout_presence"}, min_dwell_s=2.0)

    # Stage 4: purchased — POS transactions in the window.
    purchases = len(transactions_in_window(transactions, window_start, window_end))

    raw = {"entered": footfall, "engaged": engaged,
           "reached_till": reached, "purchased": purchases}

    # Monotonic render: clamp each stage to the previous.
    order = [s["key"] for s in funnel_cfg.get("stages", [])] or list(raw.keys())
    labels = {s["key"]: s["label"] for s in funnel_cfg.get("stages", [])}
    stages: List[FunnelStage] = []
    prev = None
    for key in order:
        obs = raw.get(key, 0)
        val = obs if prev is None else min(obs, prev)
        stages.append(FunnelStage(key=key, label=labels.get(key, key),
                                  observed=obs, value=val))
        prev = val

    conversion = (purchases / footfall * 100.0) if footfall > 0 else 0.0

    drop = {}
    for a, b in zip(stages, stages[1:]):
        drop[f"{a.key}->{b.key}"] = (
            (a.value - b.value) / a.value * 100.0) if a.value > 0 else 0.0

    return FunnelResult(
        window_start=window_start, window_end=window_end, stages=stages,
        footfall=footfall, purchases=purchases, conversion_pct=conversion,
        drop_off=drop,
    )
