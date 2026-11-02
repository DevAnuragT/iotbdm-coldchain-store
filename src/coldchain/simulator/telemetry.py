"""Per-shipment telemetry generation.

A shipment is a route through holders; each leg contributes a resting offset and
diurnal amplitude. On top of that we add slow AR(1) drift, sensor noise, planned
excursions (trapezoidal profiles that push the series beyond 8 °C or below 2 °C)
and sub-minute door-open blips that must *not* count as excursions.

Everything is a function of (seed, shipment_id), so shipments can be generated in
any order, in parallel, and still be identical run to run.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from coldchain.models import CustodyInterval, EngineConfig, GroundTruthExcursion, HolderType, Shipment
from coldchain.compaction.detector import find_excursions
from coldchain.simulator.world import (
    TYPE_DIURNAL_AMP,
    TYPE_EXCURSION_WEIGHT,
    TYPE_TEMP_OFFSET,
    World,
)

INTERVAL_S = 10
DAY = 86400
HOUR = 3600

PRODUCTS = ["MMR", "HepB", "OPV", "BCG", "HPV", "DTP", "Rota", "PCV"]


@dataclass(slots=True)
class SimConfig:
    seed: int = 42
    n_shipments: int = 5000
    period_start: int = 1_761_955_200  # 2025-11-01T00:00:00Z
    period_days: int = 365
    excursion_rate: float = 0.02  # fraction of shipment-time spent in excursion
    excursion_median_s: float = 1800.0  # lognormal median duration
    excursion_sigma: float = 0.7
    hot_fraction: float = 0.8
    blips_per_day: float = 0.6  # sub-minute spikes that must be ignored
    late_fraction: float = 0.05  # fraction of readings delivered late (in bursts)
    late_max_delay_s: int = 6 * HOUR
    jitter_fraction: float = 0.005  # individually re-ordered readings, delay <= 40 s
    base_delay_s: int = 2  # nominal network latency


@dataclass(slots=True)
class ShipmentTelemetry:
    shipment: Shipment
    custody: tuple[CustodyInterval, ...]
    ts: np.ndarray  # int64, strictly increasing
    temp: np.ndarray  # float64
    humidity: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    arrival: np.ndarray  # int64 processing time per reading
    planned: list[tuple[int, int, float]] = field(default_factory=list)
    ground_truth: list[GroundTruthExcursion] = field(default_factory=list)


def _leg_plan(rng: np.random.Generator, world: World, start_ts: int) -> list[tuple[int, int, int]]:
    """Return [(holder_id, from_ts, to_ts), ...] for one shipment's route."""

    def pick(t: HolderType) -> int:
        ids = world.by_type[t]
        return ids[int(rng.integers(len(ids)))]

    def dur(lo_h: float, hi_h: float) -> int:
        return int(rng.uniform(lo_h, hi_h) * HOUR) // INTERVAL_S * INTERVAL_S

    legs: list[tuple[HolderType, int]] = [(HolderType.FACTORY, dur(2, 12))]
    if rng.random() < 0.5:
        legs += [
            (HolderType.TRUCK, dur(2, 8)),
            (HolderType.AIRPORT, dur(3, 12)),
            (HolderType.TRUCK, dur(2, 8)),
        ]
    else:
        legs.append((HolderType.TRUCK, dur(4, 16)))
    legs += [
        (HolderType.DEPOT, dur(6, 48)),
        (HolderType.TRUCK, dur(1, 10)),
        (HolderType.CLINIC, dur(2, 8)),
    ]
    plan = []
    t = start_ts
    for htype, d in legs:
        plan.append((pick(htype), t, t + d))
        t += d
    return plan


def generate_shipment(cfg: SimConfig, world: World, shipment_id: int, engine_cfg: EngineConfig | None = None) -> ShipmentTelemetry:
    rng = np.random.default_rng([cfg.seed, shipment_id])
    engine_cfg = engine_cfg or EngineConfig()

    start_ts = cfg.period_start + int(rng.integers(0, cfg.period_days * DAY)) // INTERVAL_S * INTERVAL_S
    plan = _leg_plan(rng, world, start_ts)
    end_ts = plan[-1][2]
    n = (end_ts - start_ts) // INTERVAL_S
    ts = start_ts + INTERVAL_S * np.arange(n, dtype=np.int64)

    # --- per-reading leg attributes ------------------------------------------
    offset = np.empty(n)
    amp = np.empty(n)
    lat = np.empty(n)
    lon = np.empty(n)
    weight = np.empty(n)
    custody = []
    prev_site = None
    for holder_id, t0, t1 in plan:
        site = world.site(holder_id)
        sl = slice((t0 - start_ts) // INTERVAL_S, (t1 - start_ts) // INTERVAL_S)
        htype = site.holder.type
        offset[sl] = TYPE_TEMP_OFFSET[htype]
        amp[sl] = TYPE_DIURNAL_AMP[htype]
        weight[sl] = TYPE_EXCURSION_WEIGHT[htype]
        m = sl.stop - sl.start
        if htype == HolderType.TRUCK and prev_site is not None:
            # moving leg: interpolate towards the next stationary site
            nxt = world.site(plan[plan.index((holder_id, t0, t1)) + 1][0])
            f = np.linspace(0, 1, m)
            lat[sl] = prev_site.lat + (nxt.lat - prev_site.lat) * f
            lon[sl] = prev_site.lon + (nxt.lon - prev_site.lon) * f
        else:
            lat[sl] = site.lat
            lon[sl] = site.lon
            prev_site = site
        custody.append(CustodyInterval(shipment_id, holder_id, htype, t0, t1))

    # --- base temperature ------------------------------------------------------
    base = rng.normal(4.8, 0.5)
    hour_of_day = (ts % DAY) / HOUR
    diurnal = amp * np.sin(2 * np.pi * (hour_of_day - 9.0) / 24.0)
    drift = np.empty(n)
    acc = 0.0
    phi, sig = 0.999, 0.02
    eps = rng.normal(0, sig, n)
    for i in range(n):  # AR(1); kept scalar for clarity, n is small per shipment
        acc = phi * acc + eps[i]
        drift[i] = acc
    drift = np.clip(drift, -1.0, 1.0)
    temp = np.clip(base + offset + diurnal + drift, 2.8, 7.2) + rng.normal(0, 0.12, n)

    # --- planned excursions -----------------------------------------------------
    duration = n * INTERVAL_S
    mean_dur = cfg.excursion_median_s * np.exp(cfg.excursion_sigma**2 / 2)
    # ramps (15 % each side) spend part of each event inside range, so inflate
    # the event count to land the *time in excursion* near excursion_rate
    lam = cfg.excursion_rate * duration / mean_dur / 0.72
    k = int(rng.poisson(lam))
    planned: list[tuple[int, int, float]] = []
    p = weight / weight.sum()
    for _ in range(k):
        d = float(rng.lognormal(np.log(cfg.excursion_median_s), cfg.excursion_sigma))
        d = int(np.clip(d, 90, 6 * HOUR)) // INTERVAL_S * INTERVAL_S
        m = d // INTERVAL_S
        if m >= n - 2:
            continue
        i0 = int(rng.choice(n, p=p))
        i0 = min(i0, n - m - 1)
        hot = rng.random() < cfg.hot_fraction
        if hot:
            peak = engine_cfg.hi_enter_c + float(rng.uniform(0.6, 4.5))
        else:
            peak = engine_cfg.lo_enter_c - float(rng.uniform(0.6, 2.5))
        u = np.linspace(0, 1, m)
        shape = np.minimum(1.0, np.minimum(u / 0.15, (1 - u) / 0.15))
        shape = np.clip(shape, 0, 1)
        delta = peak - temp[i0 : i0 + m].mean()
        temp[i0 : i0 + m] += delta * shape + rng.normal(0, 0.08, m)
        planned.append((int(ts[i0]), int(ts[i0 + m - 1]), peak))

    # --- sub-minute blips (door openings) -------------------------------------
    n_blips = int(rng.poisson(cfg.blips_per_day * duration / DAY))
    for _ in range(n_blips):
        m = int(rng.integers(2, 6))  # 2..5 samples => span 10..40 s, never >= 60 s
        i0 = int(rng.integers(0, max(1, n - m)))
        temp[i0 : i0 + m] = np.maximum(temp[i0 : i0 + m], engine_cfg.hi_enter_c + rng.uniform(0.3, 1.5))

    humidity = np.clip(55 + 8 * np.sin(2 * np.pi * hour_of_day / 24) + rng.normal(0, 1.5, n), 20, 95)
    lat = lat + rng.normal(0, 0.0005, n)
    lon = lon + rng.normal(0, 0.0005, n)

    # --- arrival times: bursts of late data + light jitter -----------------------
    arrival = ts + cfg.base_delay_s
    if cfg.late_fraction > 0:
        mean_gap = 1.5 * HOUR
        n_gaps = int(rng.poisson(cfg.late_fraction * duration / mean_gap))
        for _ in range(n_gaps):
            g_len = int(rng.uniform(600, cfg.late_max_delay_s))
            g0 = int(rng.integers(start_ts, max(start_ts + 1, end_ts - g_len)))
            sl = (ts >= g0) & (ts < g0 + g_len)
            arrival[sl] = g0 + g_len + rng.integers(0, 30, int(sl.sum()))
    if cfg.jitter_fraction > 0:
        j = rng.random(n) < cfg.jitter_fraction
        arrival[j] += rng.integers(10, 41, int(j.sum()))

    shipment = Shipment(
        shipment_id=shipment_id,
        origin_id=plan[0][0],
        destination_id=plan[-1][0],
        start_ts=start_ts,
        end_ts=end_ts,
        product=PRODUCTS[int(rng.integers(len(PRODUCTS)))],
    )
    gt = find_excursions(ts, temp, engine_cfg, shipment_id)
    return ShipmentTelemetry(
        shipment=shipment,
        custody=tuple(custody),
        ts=ts,
        temp=temp,
        humidity=humidity,
        lat=lat,
        lon=lon,
        arrival=arrival.astype(np.int64),
        planned=planned,
        ground_truth=gt,
    )
