"""Turn generated telemetry into an arrival-ordered event stream.

The stream is what ingestion consumes (F4): ShipmentStart, then Readings in the
order they *arrive* (so late bursts show up out of event-time order), then
ShipmentEnd. Several shipments are interleaved by arrival time so the engine has
to keep per-shipment state, as it would behind a Kafka partition.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator

import numpy as np

from coldchain.models import EngineConfig, Reading, ShipmentEnd, ShipmentStart, StreamEvent
from coldchain.simulator.telemetry import ShipmentTelemetry, SimConfig, generate_shipment
from coldchain.simulator.world import World, build_world


def shipment_events(t: ShipmentTelemetry) -> Iterator[tuple[int, int, StreamEvent]]:
    """Yield (arrival_ts, seq, event) for one shipment, sorted by arrival."""
    sid = t.shipment.shipment_id
    yield (t.shipment.start_ts - 1, 0, ShipmentStart(t.shipment, t.custody))
    order = np.argsort(t.arrival, kind="stable")
    ts, temp, hum, lat, lon, arr = t.ts, t.temp, t.humidity, t.lat, t.lon, t.arrival
    seq = 1
    for i in order:
        yield (
            int(arr[i]),
            seq,
            Reading(sid, int(ts[i]), float(temp[i]), float(hum[i]), float(lat[i]), float(lon[i]), int(arr[i])),
        )
        seq += 1
    last = int(arr[order[-1]]) + 1 if len(order) else t.shipment.end_ts
    yield (last, seq, ShipmentEnd(sid, last))


class Simulator:
    def __init__(self, cfg: SimConfig, engine_cfg: EngineConfig | None = None, world: World | None = None):
        self.cfg = cfg
        self.engine_cfg = engine_cfg or EngineConfig()
        self.world = world or build_world(cfg.seed)

    def shipment(self, shipment_id: int) -> ShipmentTelemetry:
        return generate_shipment(self.cfg, self.world, shipment_id, self.engine_cfg)

    def shipment_ids(self) -> list[int]:
        return list(range(1, self.cfg.n_shipments + 1))

    def telemetry(self) -> Iterator[ShipmentTelemetry]:
        """All shipments, ordered by start time (requires a cheap pre-pass)."""
        ids = self.shipment_ids()
        starts = []
        for sid in ids:
            rng = np.random.default_rng([self.cfg.seed, sid])
            starts.append(int(rng.integers(0, self.cfg.period_days * 86400)))
        for sid in sorted(ids, key=lambda s: starts[s - 1]):
            yield self.shipment(sid)

    def events(self, interleave: int = 32) -> Iterator[StreamEvent]:
        """Arrival-ordered stream; `interleave` shipments are merged at a time."""
        key = lambda x: (x[0], x[1])  # noqa: E731 — never compare the event itself
        batch: list = []
        for t in self.telemetry():
            batch.append(shipment_events(t))
            if len(batch) >= interleave:
                for _, _, ev in heapq.merge(*batch, key=key):
                    yield ev
                batch = []
        if batch:
            for _, _, ev in heapq.merge(*batch, key=key):
                yield ev
