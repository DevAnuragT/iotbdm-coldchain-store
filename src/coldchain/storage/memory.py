"""In-memory store: used by tests and by `coldchain simulate --dry-run`.

Byte accounting mirrors the Postgres row widths so the dry run can estimate the
storage ratio without a database.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass, field

from coldchain.models import (
    CustodyInterval,
    Excursion,
    RawRow,
    Reading,
    RewriteEntry,
    Shipment,
    SummaryWindow,
)

# Approximate on-disk width per row in Postgres (tuple header + columns).
BYTES_RAW = 24 + 8 + 8 + 8 + 8 + 8 + 8 + 8  # shipment_id, ts, temp, hum, lat, lon, excursion_id
BYTES_WINDOW = 24 + 8 * 11
BYTES_EXCURSION = 24 + 8 * 8
BYTES_BASELINE = 24 + 8 * 6


@dataclass
class MemoryStore:
    shipments: dict[int, Shipment] = field(default_factory=dict)
    custody: dict[int, list[CustodyInterval]] = field(default_factory=dict)
    windows: dict[int, SummaryWindow] = field(default_factory=dict)
    excursions: dict[int, Excursion] = field(default_factory=dict)
    raw: dict[int, list[RawRow]] = field(default_factory=dict)  # per shipment
    rewrites: list[RewriteEntry] = field(default_factory=list)
    baseline: dict[tuple[int, int], tuple] = field(default_factory=dict)
    rawstore_rows: int = 0

    def shipment_started(self, shipment, custody):
        self.shipments[shipment.shipment_id] = shipment
        self.custody[shipment.shipment_id] = list(custody)

    def shipment_finished(self, shipment_id):
        pass

    def write_window(self, w):
        self.windows[w.window_id] = w

    def update_window(self, w):
        self.windows[w.window_id] = w

    def delete_window(self, shipment_id, window_id):
        self.windows.pop(window_id, None)

    def write_excursion(self, e):
        self.excursions[e.excursion_id] = e

    def update_excursion(self, e):
        self.excursions[e.excursion_id] = e

    def delete_excursion(self, shipment_id, excursion_id):
        self.excursions.pop(excursion_id, None)

    def write_raw(self, rows):
        for r in rows:
            self.raw.setdefault(r.shipment_id, []).append(r)

    def delete_raw(self, shipment_id, from_ts):
        rows = self.raw.get(shipment_id)
        if rows:
            self.raw[shipment_id] = [r for r in rows if r.ts < from_ts]

    def write_rewrite(self, entry):
        self.rewrites.append(entry)

    def find_window(self, shipment_id, ts):
        ws = sorted((w for w in self.windows.values() if w.shipment_id == shipment_id), key=lambda w: w.window_start)
        starts = [w.window_start for w in ws]
        i = bisect_right(starts, ts) - 1
        if i >= 0 and ws[i].window_end >= ts:
            return ws[i]
        return None

    def write_baseline(self, shipment_id, hour_ts, n, mean_c, min_c, max_c):
        self.baseline[(shipment_id, hour_ts)] = (n, mean_c, min_c, max_c)

    def write_rawstore(self, r):
        self.rawstore_rows += 1

    def flush(self):
        pass

    # -- helpers for tests / dry run ----------------------------------------------
    def windows_for(self, shipment_id: int) -> list[SummaryWindow]:
        return sorted((w for w in self.windows.values() if w.shipment_id == shipment_id), key=lambda w: w.window_start)

    def excursions_for(self, shipment_id: int) -> list[Excursion]:
        return sorted((e for e in self.excursions.values() if e.shipment_id == shipment_id), key=lambda e: e.start_ts)

    def raw_for(self, shipment_id: int) -> list[RawRow]:
        return sorted(self.raw.get(shipment_id, []), key=lambda r: r.ts)

    def bytes(self) -> dict[str, int]:
        n_raw = sum(len(v) for v in self.raw.values())
        return {
            "readings_raw": n_raw * BYTES_RAW,
            "summary_windows": len(self.windows) * BYTES_WINDOW,
            "excursions": len(self.excursions) * BYTES_EXCURSION,
            "excursion_aware_total": n_raw * BYTES_RAW + len(self.windows) * BYTES_WINDOW + len(self.excursions) * BYTES_EXCURSION,
            "baseline_hourly": len(self.baseline) * BYTES_BASELINE,
            "raw_store": self.rawstore_rows * (BYTES_RAW - 8),
        }
