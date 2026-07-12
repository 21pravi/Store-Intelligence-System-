"""
POS (point-of-sale) ingestion.

The CSV is line-item level (one row per product sold); a *transaction* is one
invoice_number. The funnel's "purchased" stage and the conversion rate use the
count of distinct invoices, NOT line items. We also keep per-transaction value
(GMV) and timestamp so analytics can compute revenue, basket size and align
purchases to the CCTV time window.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class Transaction:
    invoice_number: str
    ts: datetime
    salesperson: str
    items: int
    gmv: float

    @property
    def basket_size(self) -> int:
        return self.items


def _to_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def load_transactions(csv_path: str, date_str: Optional[str] = None) -> List[Transaction]:
    """Collapse line items into one Transaction per invoice_number."""
    by_inv: Dict[str, Transaction] = {}
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            inv = (row.get("invoice_number") or "").strip()
            if not inv:
                continue
            # order_date like 10-04-2026, order_time like 16:55:36
            d = (row.get("order_date") or "").strip()
            t = (row.get("order_time") or "").strip()
            try:
                ts = datetime.strptime(f"{d} {t}", "%d-%m-%Y %H:%M:%S")
            except ValueError:
                ts = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S") \
                    if d and t else datetime.min
            qty = int(_to_float(row.get("qty", "0")))
            gmv = _to_float(row.get("GMV", "0"))
            sp = (row.get("salesperson_name") or "").strip()

            tx = by_inv.get(inv)
            if tx is None:
                by_inv[inv] = Transaction(inv, ts, sp, qty, gmv)
            else:
                tx.items += qty
                tx.gmv += gmv
                if not tx.salesperson and sp:
                    tx.salesperson = sp
    return sorted(by_inv.values(), key=lambda x: x.ts)


def transactions_in_window(txs: List[Transaction], start: datetime,
                           end: datetime) -> List[Transaction]:
    return [t for t in txs if start <= t.ts <= end]


def staff_from_pos(txs: List[Transaction]) -> List[str]:
    return sorted({t.salesperson for t in txs if t.salesperson})
