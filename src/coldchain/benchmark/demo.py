"""Scripted demo scenarios against the live database.

late-data (scenario 5): a truck re-connects after N hours and uploads readings
that include an excursion inside a summary window that was already written. The
engine reopens, splits and re-compacts that window; the rewrite log shows it.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np

from coldchain.compaction.engine import CompactionEngine
from coldchain.models import EngineConfig, Reading, ShipmentEnd, ShipmentStart
from coldchain.simulator import SimConfig, Simulator
from coldchain.storage.postgres import PostgresStore

DEMO_SHIPMENT_BASE = 900_000


def _snapshot(store: PostgresStore, shipment_id: int) -> dict:
    with store.conn.cursor() as cur:
        cur.execute(
            "SELECT window_id, window_start, window_end, n, min_c, max_c, mean_c, version FROM summary_windows WHERE shipment_id=%s ORDER BY window_start",
            (shipment_id,),
        )
        windows = [
            {"window_id": r[0], "start": int(r[1].timestamp()), "end": int(r[2].timestamp()), "n": r[3], "min": r[4], "max": r[5], "mean": r[6], "version": r[7]}
            for r in cur.fetchall()
        ]
        cur.execute("SELECT excursion_id, start_ts, end_ts, peak_c, duration_s FROM excursions WHERE shipment_id=%s ORDER BY start_ts", (shipment_id,))
        excursions = [{"excursion_id": r[0], "start_ts": int(r[1].timestamp()), "end_ts": int(r[2].timestamp()), "peak_c": r[3], "duration_s": r[4]} for r in cur.fetchall()]
        cur.execute("SELECT count(*) FROM readings_raw WHERE shipment_id=%s", (shipment_id,))
        raw_rows = cur.fetchone()[0]
        cur.execute(
            "SELECT window_id, old_version, new_version, reason, late_reading_ts, rewritten_at, detail FROM rewrite_log WHERE shipment_id=%s ORDER BY id",
            (shipment_id,),
        )
        rewrites = [
            {"window_id": r[0], "old_version": r[1], "new_version": r[2], "reason": r[3], "late_reading_ts": int(r[4].timestamp()), "rewritten_at": int(r[5].timestamp()), "detail": r[6]}
            for r in cur.fetchall()
        ]
    return {"windows": windows, "excursions": excursions, "raw_rows": raw_rows, "rewrites": rewrites}


def late_data_scenario(delay_hours: float = 3.0, store: PostgresStore | None = None, seed: int | None = None) -> dict:
    """Build one fresh shipment with a single excursion, deliver everything on
    time *except* a window of readings around the excursion, which arrives
    `delay_hours` late. Return before/after views of the store."""
    own = store is None
    store = store or PostgresStore()
    seed = seed if seed is not None else int(time.time()) % 100_000
    with store.conn.cursor() as cur:
        cur.execute("SELECT coalesce(max(shipment_id), %s) FROM shipments WHERE shipment_id >= %s", (DEMO_SHIPMENT_BASE, DEMO_SHIPMENT_BASE))
        shipment_id = int(cur.fetchone()[0]) + 1
        cur.execute("SELECT count(*) FROM holders")
        if cur.fetchone()[0] == 0:
            raise RuntimeError("load a dataset first (coldchain load)")

    # one shipment with exactly one excursion of ~40 min, no other late data
    sim_cfg = SimConfig(seed=seed, n_shipments=1, excursion_rate=0.0, late_fraction=0.0, jitter_fraction=0.0, blips_per_day=0.0)
    sim = Simulator(sim_cfg, EngineConfig())
    world = sim.world
    with store.conn.cursor() as cur:  # reuse the holders already in the database
        cur.execute("SELECT holder_id FROM holders")
        known = {r[0] for r in cur.fetchall()}
    t = sim.shipment(1)
    rng = np.random.default_rng(seed)
    n = len(t.ts)
    m = 240  # 40 minutes
    i0 = int(rng.integers(n // 3, n // 2))
    u = np.linspace(0, 1, m)
    shape = np.clip(np.minimum(u / 0.15, (1 - u) / 0.15), 0, 1)
    peak = 8.0 + float(rng.uniform(1.5, 3.5))
    t.temp[i0 : i0 + m] += (peak - t.temp[i0 : i0 + m].mean()) * shape
    # the outage: readings from 15 min before the excursion to 15 min after it arrive late
    late = slice(max(0, i0 - 90), min(n, i0 + m + 90))
    arrival = t.ts + 2
    arrival[late] = int(t.ts[late.stop - 1]) + int(delay_hours * 3600) + rng.integers(0, 20, late.stop - late.start)

    sid = shipment_id
    custody = tuple(c.__class__(sid, c.holder_id, c.holder_type, c.valid_from, c.valid_to) for c in t.custody)
    if any(c.holder_id not in known for c in custody):
        store.write_holders(world.holders)
    shipment = t.shipment.__class__(sid, t.shipment.origin_id, t.shipment.destination_id, t.shipment.start_ts, t.shipment.end_ts, t.shipment.product)

    engine = CompactionEngine(store, EngineConfig())
    engine.start(shipment, custody)
    order = np.argsort(arrival, kind="stable")
    late_start_arrival = int(arrival[late.start])
    before = None
    excursion_start = int(t.ts[i0])
    for i in order:
        a = int(arrival[i])
        if before is None and a >= late_start_arrival:
            store.flush()
            before = _snapshot(store, sid)
        engine.feed(Reading(sid, int(t.ts[i]), float(t.temp[i]), float(t.humidity[i]), float(t.lat[i]), float(t.lon[i]), a))
    engine.tick(int(arrival.max()) + 10**6)
    engine.finish(sid, int(arrival.max()) + 1)
    store.flush()
    after = _snapshot(store, sid)
    if own:
        store.close()
    return {
        "shipment_id": sid,
        "delay_hours": delay_hours,
        "excursion": {"start_ts": excursion_start, "end_ts": int(t.ts[i0 + m - 1]), "peak_c": float(t.temp[i0 : i0 + m].max())},
        "late_window": {"from_ts": int(t.ts[late.start]), "to_ts": int(t.ts[late.stop - 1]), "readings": late.stop - late.start, "arrived_at": late_start_arrival},
        "before": before,
        "after": after,
        "engine": asdict(engine.stats),
    }


def run_demo(scenario: str, shipment: int) -> None:
    if scenario != "late-data":
        raise SystemExit(f"unknown scenario {scenario!r}; available: late-data")
    r = late_data_scenario()
    b, a = r["before"], r["after"]
    print(f"shipment {r['shipment_id']}: excursion {r['excursion']['start_ts']}–{r['excursion']['end_ts']} peak {r['excursion']['peak_c']:.2f}°C")
    print(f"late upload: {r['late_window']['readings']} readings {r['delay_hours']} h late\n")
    print(f"before upload: {len(b['windows'])} windows, {len(b['excursions'])} excursions, {b['raw_rows']} raw rows")
    for w in b["windows"]:
        print(f"   window {w['window_id']} v{w['version']} {datetime.fromtimestamp(w['start'], timezone.utc):%m-%d %H:%M}–{datetime.fromtimestamp(w['end'], timezone.utc):%H:%M} n={w['n']} max={w['max']:.2f}")
    print(f"after upload:  {len(a['windows'])} windows, {len(a['excursions'])} excursions, {a['raw_rows']} raw rows")
    for w in a["windows"]:
        print(f"   window {w['window_id']} v{w['version']} {datetime.fromtimestamp(w['start'], timezone.utc):%m-%d %H:%M}–{datetime.fromtimestamp(w['end'], timezone.utc):%H:%M} n={w['n']} max={w['max']:.2f}")
    for e in a["excursions"]:
        print(f"   excursion {e['excursion_id']} {e['start_ts']}–{e['end_ts']} peak {e['peak_c']:.2f} duration {e['duration_s']}s")
    print("rewrite log:")
    for x in a["rewrites"]:
        print(f"   window {x['window_id']} v{x['old_version']}→v{x['new_version']} {x['reason']}: {x['detail']}")
