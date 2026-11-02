"""Excursion recall: can an injected excursion be recovered from a store?

An excursion counts as *recovered* by the excursion-aware store when a stored
excursion for the same shipment matches its start and end within `tol_s` and its
peak within `tol_c` (N3: "start, end and peak within 10 s").

The uniform-hourly baseline cannot place anything within 10 s, so we report two
looser measures for it: `baseline_mean_recall` (an hour whose *mean* is out of
range overlaps the excursion — the naive store as the PRD describes it) and
`baseline_minmax_recall` (an hour whose min/max is out of range overlaps it —
the most generous reading of hourly rollups, which still cannot give start,
end, duration or shape).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable

from coldchain.models import EngineConfig, Excursion, GroundTruthExcursion

HOUR = 3600


def match_excursions(
    truth: Iterable[GroundTruthExcursion],
    detected: Iterable[Excursion],
    tol_s: int = 10,
    tol_c: float = 1e-3,
) -> dict:
    by_ship: dict[int, list[Excursion]] = {}
    for e in detected:
        by_ship.setdefault(e.shipment_id, []).append(e)
    for v in by_ship.values():
        v.sort(key=lambda e: e.start_ts)
    truth = list(truth)
    hits = 0
    peak_err = 0.0
    missed: list[GroundTruthExcursion] = []
    for g in truth:
        cands = by_ship.get(g.shipment_id, [])
        starts = [e.start_ts for e in cands]
        lo = bisect_left(starts, g.start_ts - tol_s)
        hi = bisect_right(starts, g.start_ts + tol_s)
        ok = None
        for e in cands[lo:hi]:
            if abs(e.end_ts - g.end_ts) <= tol_s and abs(e.peak_c - g.peak_c) <= tol_c:
                ok = e
                break
        if ok is None:
            missed.append(g)
        else:
            hits += 1
            peak_err += abs(ok.peak_c - g.peak_c)
    n = len(truth)
    return {
        "ground_truth": n,
        "detected": sum(len(v) for v in by_ship.values()),
        "recovered": hits,
        "recall": hits / n if n else 1.0,
        "peak_mae": peak_err / hits if hits else 0.0,
        "missed": missed,
    }


def baseline_recall(
    truth: Iterable[GroundTruthExcursion],
    hourly: dict[tuple[int, int], tuple[int, float, float, float]],
    cfg: EngineConfig,
) -> dict:
    """hourly: {(shipment_id, hour_ts): (n, mean, min, max)}"""
    truth = list(truth)
    mean_hits = 0
    minmax_hits = 0
    peak_err_mean = 0.0
    for g in truth:
        hours = range(g.start_ts - g.start_ts % HOUR, g.end_ts + 1, HOUR)
        rows = [hourly.get((g.shipment_id, h)) for h in hours]
        rows = [r for r in rows if r is not None]
        if any(r[1] > cfg.hi_enter_c or r[1] < cfg.lo_enter_c for r in rows):
            mean_hits += 1
        if any(r[3] > cfg.hi_enter_c or r[2] < cfg.lo_enter_c for r in rows):
            minmax_hits += 1
        if rows:
            # the "peak" a mean-only store can offer is its most extreme hourly mean
            ext = max(rows, key=lambda r: max(r[1] - cfg.hi_enter_c, cfg.lo_enter_c - r[1]))[1]
            peak_err_mean += abs(ext - g.peak_c)
    n = len(truth)
    return {
        "baseline_mean_recall": mean_hits / n if n else 1.0,
        "baseline_minmax_recall": minmax_hits / n if n else 1.0,
        "baseline_peak_mae": peak_err_mean / n if n else 0.0,
    }


def recall_in_memory(sim, store) -> dict:
    truth = [g for t in sim.telemetry() for g in t.ground_truth]
    m = match_excursions(truth, store.excursions.values())
    b = baseline_recall(truth, store.baseline, sim.engine_cfg)
    m.pop("missed")
    return {**m, **b}
