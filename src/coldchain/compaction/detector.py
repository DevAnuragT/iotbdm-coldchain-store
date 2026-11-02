"""Reference excursion detector over a complete, in-order series.

This is the *definition* of an excursion used throughout the project:

    A maximal run of readings outside [lo_enter, hi_enter] that is not broken by a
    reading inside the hysteresis band [lo_exit, hi_exit], spanning >= min_excursion_s
    from its first to its last out-of-range reading.

Readings in the gray zone (between the enter and exit thresholds) neither start,
extend nor end a run; they are simply tolerated. The online engine implements the
same rule as a state machine, so this vectorised version doubles as ground truth
for the benchmark (N3) and as the replay routine for late-data reconciliation (F8).
"""

from __future__ import annotations

import numpy as np

from coldchain.models import EngineConfig, GroundTruthExcursion

OUT, GRAY, BAND = 2, 1, 0


def classify(temp: np.ndarray, cfg: EngineConfig) -> np.ndarray:
    """Map each temperature to OUT (beyond enter threshold), BAND (inside exit
    thresholds) or GRAY (between the two)."""
    cls = np.full(temp.shape, GRAY, dtype=np.int8)
    cls[(temp > cfg.hi_enter_c) | (temp < cfg.lo_enter_c)] = OUT
    cls[(temp <= cfg.hi_exit_c) & (temp >= cfg.lo_exit_c)] = BAND
    return cls


def deviation(t: float, cfg: EngineConfig) -> float:
    """How far a temperature sits beyond the nearest enter threshold (<= 0 inside)."""
    return max(t - cfg.hi_enter_c, cfg.lo_enter_c - t)


def peak_of(seg: np.ndarray, cfg: EngineConfig) -> float:
    """The reading with the largest excursion beyond either threshold. A cold
    excursion that contains a brief hot blip still reports its cold peak."""
    dev = np.maximum(seg - cfg.hi_enter_c, cfg.lo_enter_c - seg)
    return float(seg[int(dev.argmax())])


def find_excursions(
    ts: np.ndarray, temp: np.ndarray, cfg: EngineConfig, shipment_id: int = 0
) -> list[GroundTruthExcursion]:
    """Return every excursion in an in-order series (ts strictly increasing)."""
    if len(ts) == 0:
        return []
    cls = classify(temp, cfg)
    keep = cls != GRAY
    idx = np.flatnonzero(keep)
    sub = cls[idx]
    if len(sub) == 0:
        return []
    is_out = (sub == OUT).astype(np.int8)
    d = np.diff(np.concatenate(([0], is_out, [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1) - 1  # inclusive, in collapsed index space
    out: list[GroundTruthExcursion] = []
    for s, e in zip(starts, ends):
        i0, i1 = int(idx[s]), int(idx[e])
        t0, t1 = int(ts[i0]), int(ts[i1])
        if t1 - t0 < cfg.min_excursion_s:
            continue
        seg = temp[i0 : i1 + 1]
        out.append(GroundTruthExcursion(shipment_id, t0, t1, peak_of(seg, cfg), t1 - t0))
    return out
