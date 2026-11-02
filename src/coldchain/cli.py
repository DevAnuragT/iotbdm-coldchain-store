"""coldchain command line.

  coldchain simulate   dry run through the in-memory store; prints ratio/recall
  coldchain load       run the simulator through all policies into TimescaleDB
  coldchain api        serve the query API
  coldchain bench      run the benchmark against the loaded database
  coldchain demo       scripted demo scenarios (late data, ...)
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict

import typer

from coldchain.models import EngineConfig
from coldchain.simulator import SimConfig

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)


def _progress(n: int, secs: float) -> None:
    sys.stderr.write(f"\r  {n:>12,} readings  {n / secs:>10,.0f}/s")
    sys.stderr.flush()


def _sim_config(shipments: int, seed: int, excursion_rate: float, late_fraction: float, days: int) -> SimConfig:
    return SimConfig(seed=seed, n_shipments=shipments, excursion_rate=excursion_rate, late_fraction=late_fraction, period_days=days)


def _engine_config(pre_buffer: int, post_buffer: int, max_window: int, custody_close: bool) -> EngineConfig:
    return EngineConfig(pre_buffer_s=pre_buffer * 60, post_buffer_s=post_buffer * 60, max_window_s=max_window * 3600, close_on_custody_change=custody_close)


@app.command()
def simulate(
    shipments: int = typer.Option(100, help="number of shipments"),
    seed: int = 42,
    excursion_rate: float = 0.02,
    late_fraction: float = 0.05,
    days: int = 365,
    pre_buffer: int = typer.Option(10, help="minutes of raw data kept before an excursion"),
    post_buffer: int = 10,
    max_window: int = typer.Option(6, help="max summary window span, hours"),
    custody_close: bool = True,
    json_out: bool = typer.Option(False, "--json", help="print machine-readable result"),
) -> None:
    """Dry run: simulator → engine → in-memory store. No database needed."""
    from coldchain.benchmark.recall import recall_in_memory
    from coldchain.compaction.baseline import RawStore, UniformHourly
    from coldchain.compaction.engine import CompactionEngine
    from coldchain.ingest.pipeline import run_pipeline
    from coldchain.simulator import Simulator
    from coldchain.storage.memory import MemoryStore

    sim = Simulator(_sim_config(shipments, seed, excursion_rate, late_fraction, days), _engine_config(pre_buffer, post_buffer, max_window, custody_close))
    store = MemoryStore()
    engine = CompactionEngine(store, sim.engine_cfg)
    res = run_pipeline(sim.events(), store, engine, UniformHourly(store), RawStore(store), progress=None if json_out else _progress)
    if not json_out:
        sys.stderr.write("\n")
    b = store.bytes()
    rec = recall_in_memory(sim, store)
    out = {
        "readings": res.readings,
        "seconds": round(res.seconds, 2),
        "readings_per_s": round(res.readings_per_s),
        "storage_ratio": b["excursion_aware_total"] / max(1, b["raw_store"]),
        "baseline_ratio": b["baseline_hourly"] / max(1, b["raw_store"]),
        **rec,
        "engine": res.engine_stats,
    }
    if json_out:
        print(json.dumps(out, indent=2))
    else:
        print(f"readings          {out['readings']:,}  ({out['readings_per_s']:,}/s)")
        print(f"storage ratio     {out['storage_ratio']:.2%}  (uniform hourly {out['baseline_ratio']:.2%})")
        print(f"excursions        {rec['ground_truth']} injected, {rec['detected']} detected, recall {rec['recall']:.2%}, baseline-mean recall {rec['baseline_mean_recall']:.2%}")
        print(f"late data         {res.engine_stats['late_readings']:,} late readings, {res.engine_stats['replays']} replays, {res.engine_stats['rewrites']} rewrites logged")


@app.command()
def load(
    shipments: int = typer.Option(500, help="number of shipments"),
    seed: int = 42,
    excursion_rate: float = 0.02,
    late_fraction: float = 0.05,
    days: int = 365,
    pre_buffer: int = 10,
    post_buffer: int = 10,
    max_window: int = 6,
    custody_close: bool = True,
    with_raw_store: bool = typer.Option(True, help="also fill readings_rawstore (the 'store everything' policy)"),
    run_id: str = typer.Option("", help="label for this run (default: derived from the parameters)"),
    keep: bool = typer.Option(False, help="do not truncate existing data first"),
) -> None:
    """Run the simulator through every policy into TimescaleDB."""
    from coldchain.compaction.baseline import RawStore, UniformHourly
    from coldchain.compaction.engine import CompactionEngine
    from coldchain.ingest.pipeline import run_pipeline
    from coldchain.simulator import Simulator
    from coldchain.storage.postgres import PostgresStore

    sim_cfg = _sim_config(shipments, seed, excursion_rate, late_fraction, days)
    eng_cfg = _engine_config(pre_buffer, post_buffer, max_window, custody_close)
    sim = Simulator(sim_cfg, eng_cfg)
    run_id = run_id or f"s{seed}-n{shipments}-x{excursion_rate}-l{late_fraction}"
    store = PostgresStore()
    if not keep:
        store.truncate_all()
    store.write_holders(sim.world.holders)
    store.start_run(run_id, seed, shipments, {"sim": asdict(sim_cfg), "engine": asdict(eng_cfg)})
    typer.echo(f"loading run {run_id}: {shipments} shipments, seed {seed}")

    engine = CompactionEngine(store, eng_cfg)
    res = run_pipeline(sim.events(), store, engine, UniformHourly(store), RawStore(store) if with_raw_store else None, progress=_progress)
    sys.stderr.write("\n")
    typer.echo("writing ground truth …")
    store.write_ground_truth((g.shipment_id, g.start_ts, g.end_ts, g.peak_c, g.duration_s) for t in sim.telemetry() for g in t.ground_truth)
    store.finish_run(run_id, res.readings, res.seconds, res.engine_stats)
    store.close()
    typer.echo(f"done: {res.readings:,} readings in {res.seconds:.1f}s ({res.readings_per_s:,.0f}/s); engine {res.engine_stats}")


@app.command()
def produce(
    shipments: int = 100,
    seed: int = 42,
    excursion_rate: float = 0.02,
    late_fraction: float = 0.05,
    days: int = 365,
    bootstrap: str = typer.Option("localhost:19092", envvar="COLDCHAIN_KAFKA"),
    topic: str = typer.Option("readings", envvar="COLDCHAIN_TOPIC"),
) -> None:
    """Publish the simulated stream to a Redpanda/Kafka topic (needs the kafka extra)."""
    from coldchain.ingest.kafka import produce as _produce
    from coldchain.simulator import Simulator

    sim = Simulator(_sim_config(shipments, seed, excursion_rate, late_fraction, days))
    n = _produce(sim.events(), bootstrap, topic, progress=lambda n: sys.stderr.write(f"\r  {n:>12,} events"))
    sys.stderr.write("\n")
    typer.echo(f"published {n:,} events to {topic} @ {bootstrap}")


@app.command()
def compactor(
    bootstrap: str = typer.Option("localhost:19092", envvar="COLDCHAIN_KAFKA"),
    topic: str = typer.Option("readings", envvar="COLDCHAIN_TOPIC"),
    group: str = "compactor",
    idle_timeout: float = typer.Option(15.0, help="stop after this many quiet seconds"),
    with_raw_store: bool = False,
    truncate: bool = typer.Option(False, help="truncate existing data first"),
    run_id: str = "kafka",
) -> None:
    """Consume the topic through every policy into TimescaleDB."""
    from coldchain.compaction.baseline import RawStore, UniformHourly
    from coldchain.compaction.engine import CompactionEngine
    from coldchain.ingest.kafka import consume
    from coldchain.ingest.pipeline import run_pipeline
    from coldchain.simulator import build_world
    from coldchain.storage.postgres import PostgresStore

    store = PostgresStore()
    if truncate:
        store.truncate_all()
    store.write_holders(build_world(42).holders)
    store.start_run(run_id, 42, 0, {"source": "kafka", "topic": topic})
    engine = CompactionEngine(store, EngineConfig())
    res = run_pipeline(consume(bootstrap, topic, group, idle_timeout), store, engine, UniformHourly(store), RawStore(store) if with_raw_store else None, progress=_progress)
    sys.stderr.write("\n")
    store.finish_run(run_id, res.readings, res.seconds, res.engine_stats)
    store.close()
    typer.echo(f"consumed {res.readings:,} readings in {res.seconds:.1f}s ({res.readings_per_s:,.0f}/s); engine {res.engine_stats}")


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Serve the query API (FastAPI)."""
    import uvicorn

    uvicorn.run("coldchain.api.app:app", host=host, port=port, reload=reload)


@app.command()
def bench(
    run_id: str = typer.Option("", help="run to benchmark (default: latest)"),
    queries: int = typer.Option(50, help="runs per scenario query for p95 latency"),
    compress: bool = typer.Option(False, help="also measure sizes after TimescaleDB compression"),
    out_dir: str = "reports",
) -> None:
    """Measure bytes, recall, peak error and p95 latency per policy (F15)."""
    from coldchain.benchmark.run import run_benchmark

    run_benchmark(run_id or None, queries, compress, out_dir)


@app.command()
def sweep(
    shipments: int = 100,
    seed: int = 42,
    out: str = "reports/sensitivity.json",
) -> None:
    """Sensitivity runs (in memory): excursion rate × buffer length × late fraction."""
    from coldchain.benchmark.sweep import run_sweep

    run_sweep(shipments, seed, out)


@app.command()
def demo(scenario: str = typer.Argument("late-data"), shipment: int = 0) -> None:
    """Scripted demo scenarios against the live database."""
    from coldchain.benchmark.demo import run_demo

    run_demo(scenario, shipment)


if __name__ == "__main__":
    app()
