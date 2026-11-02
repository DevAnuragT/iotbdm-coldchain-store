"""Comparison policies fed from the same stream (F13, F15).

UniformHourly  — the naive baseline: one row per shipment-hour with mean/min/max.
                 Buckets are held until the shipment ends so late data is handled
                 as generously as a continuous aggregate would.
RawStore       — every reading, for the "store everything" policy.
"""

from __future__ import annotations

from coldchain.models import Reading, ShipmentEnd, ShipmentStart, StreamEvent
from coldchain.storage.base import Store

HOUR = 3600


class UniformHourly:
    def __init__(self, store: Store):
        self.store = store
        self.buckets: dict[int, dict[int, list]] = {}
        self.rows = 0

    def process(self, ev: StreamEvent) -> None:
        if type(ev) is Reading:
            b = self.buckets.setdefault(ev.shipment_id, {})
            h = ev.ts - ev.ts % HOUR
            cell = b.get(h)
            t = ev.temp_c
            if cell is None:
                b[h] = [1, t, t, t]
            else:
                cell[0] += 1
                cell[1] += t
                if t < cell[2]:
                    cell[2] = t
                elif t > cell[3]:
                    cell[3] = t
        elif type(ev) is ShipmentEnd:
            b = self.buckets.pop(ev.shipment_id, {})
            for h, (n, s, lo, hi) in sorted(b.items()):
                self.store.write_baseline(ev.shipment_id, h, n, s / n, lo, hi)
                self.rows += 1


class RawStore:
    def __init__(self, store: Store):
        self.store = store
        self.rows = 0

    def process(self, ev: StreamEvent) -> None:
        if type(ev) is Reading:
            self.store.write_rawstore(ev)
            self.rows += 1
