"""Simulator properties and the end-to-end in-memory pipeline (milestone exit
criteria: ≤ 10 % storage ratio and 100 % recall on a 100-shipment set)."""

from __future__ import annotations

import numpy as np
import pytest

from coldchain.benchmark.recall import match_excursions
from coldchain.compaction.baseline import RawStore, UniformHourly
from coldchain.compaction.engine import CompactionEngine
from coldchain.ingest.pipeline import run_pipeline, validate, ValidationError
from coldchain.models import Reading
from coldchain.simulator import SimConfig, Simulator
from coldchain.storage.memory import MemoryStore


def test_simulator_is_deterministic():
    a = Simulator(SimConfig(seed=11, n_shipments=5)).shipment(3)
    b = Simulator(SimConfig(seed=11, n_shipments=5)).shipment(3)
    assert np.array_equal(a.temp, b.temp) and np.array_equal(a.arrival, b.arrival)
    assert a.custody == b.custody and a.ground_truth == b.ground_truth


def test_simulator_shape_and_custody():
    t = Simulator(SimConfig(seed=11, n_shipments=5)).shipment(1)
    assert np.all(np.diff(t.ts) == 10)
    assert t.custody[0].valid_from == t.shipment.start_ts and t.custody[-1].valid_to == t.shipment.end_ts
    assert all(a.valid_to == b.valid_from for a, b in zip(t.custody, t.custody[1:]))
    assert t.custody[0].holder_type.value == "factory" and t.custody[-1].holder_type.value == "clinic"
    assert (t.arrival >= t.ts).all()


def test_late_fraction_is_roughly_configured():
    sim = Simulator(SimConfig(seed=11, n_shipments=40, late_fraction=0.05))
    late = tot = 0
    for t in sim.telemetry():
        late += int(((t.arrival - t.ts) > 60).sum())
        tot += len(t.ts)
    assert 0.02 < late / tot < 0.10
    assert all((t.arrival - t.ts).max() <= 6 * 3600 + 60 for t in sim.telemetry())


def test_validation_rejects_bad_units():
    with pytest.raises(ValidationError):
        validate(Reading(1, 0, 278.0, 50, 0, 0, 2))  # Kelvin
    with pytest.raises(ValidationError):
        validate(Reading(1, 0, 5.0, 150, 0, 0, 2))
    validate(Reading(1, 0, 5.0, 50, 0, 0, 2))


@pytest.mark.parametrize("late_fraction", [0.0, 0.05, 0.2])
def test_hundred_shipments_ratio_and_recall(late_fraction):
    sim = Simulator(SimConfig(seed=42, n_shipments=100, late_fraction=late_fraction))
    store = MemoryStore()
    res = run_pipeline(sim.events(), store, CompactionEngine(store, sim.engine_cfg), UniformHourly(store), RawStore(store))
    b = store.bytes()
    assert b["excursion_aware_total"] / b["raw_store"] <= 0.10
    truth = [g for t in sim.telemetry() for g in t.ground_truth]
    m = match_excursions(truth, store.excursions.values())
    assert m["recall"] == 1.0, m["missed"][:3]
    assert m["peak_mae"] == 0.0
    # every reading is stored exactly once: either summarised or raw
    assert sum(w.n for w in store.windows.values()) + sum(len(v) for v in store.raw.values()) == res.readings
    assert res.engine_stats["unreconstructable"] == 0


def test_raw_coverage_around_every_excursion():
    sim = Simulator(SimConfig(seed=7, n_shipments=40, late_fraction=0.1))
    store = MemoryStore()
    run_pipeline(sim.events(), store, CompactionEngine(store, sim.engine_cfg))
    for t in sim.telemetry():
        have = {r.ts for r in store.raw.get(t.shipment.shipment_id, [])}
        for g in t.ground_truth:
            for ts in range(g.start_ts - 600, g.end_ts + 601, 10):
                if t.shipment.start_ts <= ts < t.shipment.end_ts:
                    assert ts in have, (t.shipment.shipment_id, g, ts)
