"""Unit tests for the compaction engine against hand-built streams."""

from __future__ import annotations

import numpy as np
import pytest

from coldchain.compaction.detector import find_excursions
from coldchain.compaction.engine import CompactionEngine
from coldchain.models import CustodyInterval, EngineConfig, HolderType, Reading, Shipment
from coldchain.storage.memory import MemoryStore

T0 = 1_700_000_000
SID = 1


def make(temps: list[float], custody_breaks: list[int] = (), delays: dict[int, int] | None = None):
    """Build a shipment whose readings follow `temps` at 10 s. `delays` maps reading
    index → seconds late. Returns (engine, store, readings sorted by arrival)."""
    store = MemoryStore()
    eng = CompactionEngine(store, EngineConfig())
    n = len(temps)
    end = T0 + 10 * n
    bounds = [T0, *[T0 + 10 * i for i in custody_breaks], end]
    custody = [CustodyInterval(SID, 10 + k, HolderType.TRUCK, a, b) for k, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    eng.start(Shipment(SID, 10, 10 + len(custody) - 1, T0, end, "MMR"), custody)
    readings = []
    for i, t in enumerate(temps):
        ts = T0 + 10 * i
        readings.append(Reading(SID, ts, t, 50.0, 10.0, 70.0, ts + 2 + (delays or {}).get(i, 0)))
    readings.sort(key=lambda r: (r.arrival_ts, r.ts))
    return eng, store, readings


def run(eng, store, readings):
    for r in readings:
        eng.feed(r)
    eng.finish(SID, readings[-1].arrival_ts + 1)
    return store


def temps_with_excursion(n: int, start: int, length: int, peak: float = 10.0, base: float = 5.0) -> list[float]:
    t = [base] * n
    for i in range(start, start + length):
        t[i] = peak
    return t


def test_steady_shipment_is_one_window_no_raw():
    eng, store, rs = make([5.0] * 720)  # 2 h
    run(eng, store, rs)
    ws = store.windows_for(SID)
    assert len(ws) == 1 and ws[0].n == 720 and ws[0].window_start == T0 and ws[0].window_end == T0 + 7190
    assert store.excursions_for(SID) == [] and store.raw_for(SID) == []


def test_excursion_kept_raw_with_buffers_and_window_split():
    n, s, L = 1440, 700, 30  # 4 h, 5 min excursion at 10 °C
    eng, store, rs = make(temps_with_excursion(n, s, L))
    run(eng, store, rs)
    ex = store.excursions_for(SID)
    assert len(ex) == 1
    e = ex[0]
    assert e.start_ts == T0 + 10 * s and e.end_ts == T0 + 10 * (s + L - 1) and e.peak_c == 10.0 and e.kind == "hot"
    raw_ts = [r.ts for r in store.raw_for(SID)]
    assert raw_ts[0] == e.start_ts - 600, "pre-buffer is exactly 10 min"
    assert raw_ts[-1] == e.end_ts + 600, "post-buffer is exactly 10 min"
    assert raw_ts == list(range(raw_ts[0], raw_ts[-1] + 1, 10))
    assert all(r.excursion_id == e.excursion_id for r in store.raw_for(SID))
    ws = store.windows_for(SID)
    assert len(ws) == 2
    assert ws[0].window_end == raw_ts[0] - 10 and ws[1].window_start == raw_ts[-1] + 10
    assert ws[0].n + ws[1].n + len(raw_ts) == n


def test_sub_minute_blip_is_not_an_excursion_but_shows_in_window_max():
    temps = temps_with_excursion(720, 300, 5)  # 50 s span (5 samples) -> below 60 s
    eng, store, rs = make(temps)
    run(eng, store, rs)
    assert store.excursions_for(SID) == []
    assert store.raw_for(SID) == []
    assert store.windows_for(SID)[0].max_c == 10.0


def test_exactly_sixty_seconds_is_an_excursion():
    temps = temps_with_excursion(720, 300, 7)  # 7 samples -> 60 s span
    eng, store, rs = make(temps)
    run(eng, store, rs)
    assert len(store.excursions_for(SID)) == 1
    assert store.excursions_for(SID)[0].duration_s == 60


def test_hysteresis_gray_zone_does_not_end_excursion():
    temps = [5.0] * 300 + [9.0] * 12 + [7.8] * 30 + [9.0] * 12 + [5.0] * 300
    eng, store, rs = make(temps)
    run(eng, store, rs)
    ex = store.excursions_for(SID)
    assert len(ex) == 1, "7.8 °C is inside the hysteresis band (> 7.5): still one excursion"
    assert ex[0].duration_s == 10 * (12 + 30 + 12 - 1)


def test_band_return_separates_excursions():
    temps = [5.0] * 300 + [9.0] * 12 + [6.0] * 3 + [9.0] * 12 + [5.0] * 300
    eng, store, rs = make(temps)
    run(eng, store, rs)
    assert len(store.excursions_for(SID)) == 2


def test_cold_excursion_peak_is_the_minimum():
    temps = [5.0] * 300 + [0.5] * 20 + [5.0] * 300
    eng, store, rs = make(temps)
    run(eng, store, rs)
    e = store.excursions_for(SID)[0]
    assert e.kind == "cold" and e.peak_c == 0.5


def test_custody_change_closes_window():
    eng, store, rs = make([5.0] * 720, custody_breaks=[360])
    run(eng, store, rs)
    ws = store.windows_for(SID)
    assert len(ws) == 2 and ws[0].window_end == T0 + 3590 and ws[1].window_start == T0 + 3600


def test_max_span_closes_window():
    eng, store, rs = make([5.0] * (6 * 360 + 60))  # 6 h 10 min
    run(eng, store, rs)
    ws = store.windows_for(SID)
    assert len(ws) == 2 and ws[0].window_end - ws[0].window_start == 6 * 3600 - 10


def test_window_stats_are_exact():
    rng = np.random.default_rng(1)
    temps = list(np.clip(rng.normal(5, 0.5, 720), 3, 7))
    eng, store, rs = make(temps)
    run(eng, store, rs)
    w = store.windows_for(SID)[0]
    a = np.array(temps)
    assert w.n == 720
    assert abs(w.mean_c - a.mean()) < 1e-9 and abs(w.sd_c - a.std(ddof=1)) < 1e-9
    assert w.min_c == a.min() and w.max_c == a.max() and w.last_c == temps[-1]


def test_ground_truth_detector_agrees_with_engine():
    rng = np.random.default_rng(3)
    temps = list(np.clip(rng.normal(5, 0.4, 2000), 3, 7))
    for s, L, p in ((200, 30, 10.0), (600, 4, 9.0), (900, 7, 8.6), (1400, 50, 1.0)):
        for i in range(s, s + L):
            temps[i] = p
    eng, store, rs = make(temps)
    run(eng, store, rs)
    ts = np.array([T0 + 10 * i for i in range(len(temps))])
    gt = find_excursions(ts, np.array(temps), EngineConfig(), SID)
    got = [(e.start_ts, e.end_ts, e.peak_c) for e in store.excursions_for(SID)]
    assert got == [(g.start_ts, g.end_ts, g.peak_c) for g in gt]
    assert len(gt) == 3  # the 4-sample blip is not an excursion


# ----------------------------------------------------------------- late data
def test_late_in_range_reading_merges_into_closed_window_with_version_bump():
    temps = [5.0] * 1440
    delays = {100: 5 * 3600}  # one reading 5 h late, lands in a closed window
    eng, store, rs = make(temps, custody_breaks=[360], delays=delays)
    run(eng, store, rs)
    ws = store.windows_for(SID)
    assert ws[0].n == 360 and ws[0].version == 2
    assert [r.reason for r in store.rewrites] == ["merge"]


def test_late_excursion_splits_closed_window_and_logs_rewrite():
    n, s, L = 1440, 500, 30
    temps = temps_with_excursion(n, s, L)
    # the excursion and 15 min around it arrive 3 h late (an outage), everything else on time
    delays = {i: 3 * 3600 for i in range(s - 90, s + L + 90)}
    eng, store, rs = make(temps, custody_breaks=[720], delays=delays)
    # before the late burst arrives, the first custody window is closed and clean
    first_arrival_of_burst = min(r.arrival_ts for r in rs if r.ts == T0 + 10 * (s - 90))
    for r in rs:
        if r.arrival_ts >= first_arrival_of_burst:
            break
        eng.feed(r)
    assert len(store.windows_for(SID)) == 1 and store.excursions_for(SID) == []
    v0 = store.windows_for(SID)[0].version
    wid = store.windows_for(SID)[0].window_id
    for r in rs:
        if r.arrival_ts >= first_arrival_of_burst:
            eng.feed(r)
    eng.finish(SID, rs[-1].arrival_ts + 1)
    ex = store.excursions_for(SID)
    assert len(ex) == 1 and ex[0].start_ts == T0 + 10 * s and ex[0].end_ts == T0 + 10 * (s + L - 1)
    raw_ts = [r.ts for r in store.raw_for(SID)]
    assert raw_ts[0] == ex[0].start_ts - 600 and raw_ts[-1] == ex[0].end_ts + 600
    ws = store.windows_for(SID)
    assert ws[0].window_id == wid and ws[0].version > v0, "reopened window keeps its id, bumps version"
    assert sum(w.n for w in ws) + len(raw_ts) == n
    reasons = [r.reason for r in store.rewrites]
    assert "split" in reasons
    split = next(r for r in store.rewrites if r.reason == "split")
    assert split.window_id == wid and split.new_version == split.old_version + 1


def test_late_band_reading_inside_excursion_splits_it():
    # a 2-run excursion whose separating in-band readings arrive late
    temps = [5.0] * 300 + [9.0] * 12 + [6.0] * 3 + [9.0] * 12 + [5.0] * 300
    delays = {312: 7200, 313: 7200, 314: 7200}
    eng, store, rs = make(temps, delays=delays)
    run(eng, store, rs)
    assert len(store.excursions_for(SID)) == 2


def test_out_of_range_late_reading_beyond_horizon_is_kept_raw_and_logged():
    n = 24 * 360  # one day
    temps = [5.0] * n
    temps[100] = 10.0
    delays = {100: 20 * 3600}  # 20 h late: beyond the 12 h staging horizon
    eng, store, rs = make(temps, custody_breaks=[360], delays=delays)
    run(eng, store, rs)
    assert store.excursions_for(SID) == []
    raw = store.raw_for(SID)
    assert len(raw) == 1 and raw[0].ts == T0 + 1000 and raw[0].excursion_id is None
    assert store.windows_for(SID)[0].max_c == 10.0
    assert [r.reason for r in store.rewrites] == ["beyond_horizon"]
    assert eng.stats.unreconstructable == 1


def test_every_reading_accounted_exactly_once_under_heavy_reordering():
    rng = np.random.default_rng(5)
    n = 3 * 360
    temps = list(np.clip(rng.normal(5, 0.4, n), 3, 7))
    for i in range(400, 440):
        temps[i] = 9.5
    delays = {i: int(rng.integers(0, 1800)) for i in rng.choice(n, 300, replace=False)}
    eng, store, rs = make(temps, delays=delays)
    run(eng, store, rs)
    assert sum(w.n for w in store.windows_for(SID)) + len(store.raw_for(SID)) == n
    assert len({r.ts for r in store.raw_for(SID)}) == len(store.raw_for(SID))
    assert len(store.excursions_for(SID)) == 1
