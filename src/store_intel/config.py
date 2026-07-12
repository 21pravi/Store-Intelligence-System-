"""Typed loader for config/store_config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time
from functools import lru_cache
from typing import List, Optional, Tuple

import yaml

Point = Tuple[float, float]


@dataclass
class Zone:
    name: str
    type: str               # browse | checkout | staff_only
    polygon: List[Point]


@dataclass
class Tripwire:
    p1: Point
    p2: Point
    entry_direction: str    # left | right | up | down
    min_cross_disp: float = 0.04
    debounce_seconds: float = 3.0


@dataclass
class CameraConfig:
    id: str
    file: str
    role: str               # entrance | browse | checkout | backroom
    zone: str
    start_time: datetime
    zones: List[Zone] = field(default_factory=list)
    tripwire: Optional[Tripwire] = None


@dataclass
class StoreConfig:
    store_id: str
    name: str
    city: str
    timezone: str
    open_time: time
    close_time: time
    expected_staff: int


@dataclass
class AppConfig:
    store: StoreConfig
    cameras: List[CameraConfig]
    detector: dict
    funnel: dict
    staff_rules: dict
    anomaly: dict

    def camera(self, cam_id: str) -> CameraConfig:
        for c in self.cameras:
            if c.id == cam_id:
                return c
        raise KeyError(cam_id)


def _parse_time(s: str) -> time:
    return datetime.strptime(s, "%H:%M:%S").time()


def load_config(path: Optional[str] = None) -> AppConfig:
    path = path or os.environ.get(
        "STORE_CONFIG",
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "store_config.yaml"),
    )
    with open(path, "r") as fh:
        raw = yaml.safe_load(fh)

    s = raw["store"]
    store = StoreConfig(
        store_id=s["store_id"], name=s["name"], city=s["city"], timezone=s["timezone"],
        open_time=_parse_time(s["open_time"]), close_time=_parse_time(s["close_time"]),
        expected_staff=int(s["expected_staff"]),
    )

    cameras: List[CameraConfig] = []
    for c in raw["cameras"]:
        zones = [Zone(z["name"], z["type"], [tuple(p) for p in z["polygon"]])
                 for z in c.get("zones", [])]
        tw = None
        if c.get("tripwire"):
            t = c["tripwire"]
            tw = Tripwire(
                p1=tuple(t["p1"]), p2=tuple(t["p2"]),
                entry_direction=t["entry_direction"],
                min_cross_disp=float(t.get("min_cross_disp", 0.04)),
                debounce_seconds=float(t.get("debounce_seconds", 3.0)),
            )
        cameras.append(CameraConfig(
            id=c["id"], file=c["file"], role=c["role"], zone=c["zone"],
            start_time=datetime.fromisoformat(c["start_time"]),
            zones=zones, tripwire=tw,
        ))

    return AppConfig(
        store=store, cameras=cameras,
        detector=raw.get("detector", {}), funnel=raw.get("funnel", {}),
        staff_rules=raw.get("staff_rules", {}), anomaly=raw.get("anomaly", {}),
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    return load_config()
