"""Ingestion (F4): pull events from a stream and push them through the policies.

The stream is any iterable of StreamEvent — the in-process simulator by default,
or the Kafka consumer in `coldchain.ingest.kafka`. Schema/unit validation happens
here, once, before any policy sees a reading.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field

from coldchain.compaction.baseline import RawStore, UniformHourly
from coldchain.compaction.engine import CompactionEngine
from coldchain.models import Reading, ShipmentEnd, StreamEvent
from coldchain.storage.base import Store


class ValidationError(ValueError):
    pass


def validate(r: Reading) -> None:
    # Units: °C and %RH. A sensor reporting Kelvin or Fahrenheit lands far
    # outside this band and is rejected rather than silently stored.
    if not (-40.0 <= r.temp_c <= 60.0):
        raise ValidationError(f"temp_c out of plausible range: {r.temp_c}")
    if not (0.0 <= r.humidity_pct <= 100.0):
        raise ValidationError(f"humidity_pct out of range: {r.humidity_pct}")
    if not (-90.0 <= r.lat <= 90.0 and -180.0 <= r.lon <= 180.0):
        raise ValidationError("bad coordinates")
    if r.arrival_ts < r.ts:
        raise ValidationError("arrival before event time")


@dataclass
class PipelineResult:
    readings: int = 0
    rejected: int = 0
    seconds: float = 0.0
    engine_stats: dict = field(default_factory=dict)

    @property
    def readings_per_s(self) -> float:
        return self.readings / self.seconds if self.seconds else 0.0


def run_pipeline(
    events: Iterable[StreamEvent],
    store: Store,
    engine: CompactionEngine,
    baseline: UniformHourly | None = None,
    rawstore: RawStore | None = None,
    progress: Callable[[int, float], None] | None = None,
    tick_every: int = 20_000,
    flush_every: int = 200_000,
) -> PipelineResult:
    res = PipelineResult()
    t0 = time.perf_counter()
    n = 0
    last_arrival = 0
    policies = [p for p in (baseline, rawstore) if p is not None]
    for ev in events:
        if type(ev) is Reading:
            try:
                validate(ev)
            except ValidationError:
                res.rejected += 1
                continue
            n += 1
            last_arrival = ev.arrival_ts
            engine.feed(ev)
            for p in policies:
                p.process(ev)
            if n % tick_every == 0:
                engine.tick(last_arrival)
                if progress:
                    progress(n, time.perf_counter() - t0)
            if n % flush_every == 0:
                store.flush()
        else:
            engine.process(ev)
            for p in policies:
                p.process(ev)
    engine.tick(last_arrival + 10**9)
    store.flush()
    res.readings = n
    res.seconds = time.perf_counter() - t0
    res.engine_stats = asdict(engine.stats)
    return res
