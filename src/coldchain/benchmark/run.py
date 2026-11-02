"""Benchmark (F15): bytes stored, excursion recall, peak error and p95 query
latency for the three policies, measured against the loaded database.

Results go to `benchmark_results` (so the dashboard can show them), to
reports/benchmark-<run>.json and reports/benchmark-<run>.md, and to two plots.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from coldchain.api import queries
from coldchain.benchmark.recall import baseline_recall, match_excursions
from coldchain.models import EngineConfig, Excursion, GroundTruthExcursion
from coldchain.storage.postgres import PostgresStore, dsn


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * len(xs))) - 1)] if xs else 0.0


def _latency(conn, fn, n: int, *args) -> dict:
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn(conn, *args)
        times.append(time.perf_counter() - t0)
    return {"p50_ms": statistics.median(times) * 1000, "p95_ms": _p95(times) * 1000, "max_ms": max(times) * 1000, "runs": n}


def _compress(conn) -> None:
    """Enable TimescaleDB columnar compression on every hypertable and compress all chunks."""
    with conn.cursor() as cur:
        for t, seg in (("readings_rawstore", "shipment_id"), ("readings_raw", "shipment_id"), ("summary_windows", "shipment_id"), ("baseline_hourly", "shipment_id")):
            cur.execute(f"ALTER TABLE {t} SET (timescaledb.compress, timescaledb.compress_segmentby = '{seg}')")
            cur.execute(f"SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks('{t}') c")
    conn.commit()


def _decompress(conn) -> None:
    with conn.cursor() as cur:
        for t in ("readings_rawstore", "readings_raw", "summary_windows", "baseline_hourly"):
            cur.execute(f"SELECT decompress_chunk(c, if_compressed => true) FROM show_chunks('{t}') c")
            cur.execute(f"ALTER TABLE {t} SET (timescaledb.compress = false)")
    conn.commit()


def run_benchmark(run_id: str | None, n_queries: int, compress: bool, out_dir: str) -> dict:
    store = PostgresStore()
    conn = store.conn
    qconn = psycopg.connect(dsn(), row_factory=dict_row, autocommit=True)
    cfg = EngineConfig()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1" if run_id is None else "SELECT * FROM runs WHERE run_id = %s", () if run_id is None else (run_id,))
        row = cur.fetchone()
        if row is None:
            raise SystemExit("no run found; load data first")
        run_id, seed, n_ships, config, readings, seconds, stats = row[0], row[1], row[2], row[3], row[6], row[7], row[8]
        eng = config.get("engine", {}) if isinstance(config, dict) else {}
        cfg = EngineConfig.from_dict(eng) if eng else cfg
        cur.execute("SELECT shipment_id, start_ts, end_ts, peak_c, duration_s FROM ground_truth")
        truth = [GroundTruthExcursion(r[0], _ts(r[1]), _ts(r[2]), float(r[3]), r[4]) for r in cur.fetchall()]
        cur.execute("SELECT excursion_id, shipment_id, start_ts, end_ts, peak_c, duration_s, detected_at, kind FROM excursions WHERE shipment_id < 900000")
        detected = [Excursion(r[0], r[1], _ts(r[2]), _ts(r[3]), float(r[4]), r[5], _ts(r[6]), r[7]) for r in cur.fetchall()]
        cur.execute("SELECT shipment_id, hour, n, mean_c, min_c, max_c FROM baseline_hourly")
        hourly = {(r[0], _ts(r[1])): (r[2], float(r[3]), float(r[4]), float(r[5])) for r in cur.fetchall()}

    print(f"benchmark run {run_id}: {n_ships} shipments, {readings:,} readings, {len(truth)} injected excursions")

    # --- bytes ---------------------------------------------------------------
    sizes = store.table_bytes()
    policies = {
        "raw": sizes["readings_rawstore"],
        "uniform_hourly": sizes["baseline_hourly"],
        "excursion_aware": sizes["readings_raw"] + sizes["summary_windows"] + sizes["excursions"],
    }
    compressed = None
    if compress:
        print("compressing hypertables …")
        _compress(conn)
        cs = store.table_bytes()
        compressed = {
            "raw": cs["readings_rawstore"],
            "uniform_hourly": cs["baseline_hourly"],
            "excursion_aware": cs["readings_raw"] + cs["summary_windows"] + cs["excursions"],
        }
        _decompress(conn)

    # --- recall / peak error ------------------------------------------------------
    m = match_excursions(truth, detected)
    b = baseline_recall(truth, hourly, cfg)
    missed = [(g.shipment_id, g.start_ts, g.end_ts, g.peak_c) for g in m["missed"]]

    # --- latency (50 runs each of scenarios 1–3 plus the custody join) ---------------
    print("timing queries …")
    sample_ships = [e.shipment_id for e in detected[: max(1, n_queries)]] or [1]
    lat = {
        "scenario1_hidden": _latency(qconn, queries.hidden_excursions, n_queries, 50, 0),
        "scenario2_custody_list": _latency(qconn, queries.list_excursions, n_queries, None, None, None, None, None, None, "start", 50, 0),
        "scenario2_custody_detail": _latency(qconn, lambda c, i: queries.excursion_detail(c, detected[i % len(detected)].excursion_id) if detected else None, n_queries, 0),
        "scenario3_route_risk": _latency(qconn, queries.route_risk, n_queries, "holder", 50),
        "trace_full_shipment": _latency(qconn, lambda c, i: queries.trace(c, sample_ships[i % len(sample_ships)], None, None, "excursion_aware"), n_queries, 0),
        "trace_baseline": _latency(qconn, lambda c, i: queries.trace(c, sample_ships[i % len(sample_ships)], None, None, "baseline"), n_queries, 0),
        "storage_audit": _latency(qconn, queries.storage, n_queries, None),
    }
    # the raw policy answers the same trace question from readings_rawstore
    if sizes["readings_rawstore"] > 0:
        lat["trace_raw_policy"] = _latency(qconn, lambda c, i: queries.trace(c, sample_ships[i % len(sample_ships)], None, None, "raw"), n_queries, 0)

    # --- late-data latency: from the engine's point of view, a late reading is
    # reflected once the debounce (reconcile_delay_s) elapses and the batch is
    # flushed; we report the configured bound and the observed replay count ---------------
    late = {
        "bound_s": cfg.reconcile_delay_s,
        "max_wait_s": cfg.reconcile_max_wait_s,
        "late_readings": (stats or {}).get("late_readings"),
        "replays": (stats or {}).get("replays"),
        "rewrites": (stats or {}).get("rewrites"),
        "unreconstructable": (stats or {}).get("unreconstructable"),
    }

    result = {
        "run_id": run_id, "seed": seed, "shipments": n_ships, "readings": readings,
        "ingest": {"seconds": seconds, "readings_per_s": readings / seconds if seconds else None},
        "bytes": policies, "bytes_compressed": compressed,
        "ratio": {k: v / policies["raw"] if policies["raw"] else None for k, v in policies.items()},
        "ratio_compressed": {k: v / compressed["raw"] for k, v in compressed.items()} if compressed else None,
        "recall": {
            "ground_truth": m["ground_truth"], "excursion_aware": m["recall"], "excursion_aware_peak_mae": m["peak_mae"],
            "raw": 1.0, "raw_peak_mae": 0.0,
            "uniform_hourly_mean": b["baseline_mean_recall"], "uniform_hourly_minmax": b["baseline_minmax_recall"],
            "uniform_hourly_peak_mae": b["baseline_peak_mae"], "missed": missed[:20],
        },
        "latency": lat,
        "late_data": late,
        "tables": sizes,
        "engine_config": eng,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    # --- persist --------------------------------------------------------------------
    for pol in ("raw", "uniform_hourly", "excursion_aware"):
        store.record_result(run_id, pol, "bytes", policies[pol], "B")
        store.record_result(run_id, pol, "ratio", result["ratio"][pol], "fraction")
        if compressed:
            store.record_result(run_id, pol, "bytes_compressed", compressed[pol], "B")
    store.record_result(run_id, "excursion_aware", "recall", m["recall"], "fraction", {"missed": missed[:20]})
    store.record_result(run_id, "excursion_aware", "peak_mae", m["peak_mae"], "C")
    store.record_result(run_id, "raw", "recall", 1.0, "fraction")
    store.record_result(run_id, "raw", "peak_mae", 0.0, "C")
    store.record_result(run_id, "uniform_hourly", "recall", b["baseline_mean_recall"], "fraction", {"minmax_recall": b["baseline_minmax_recall"]})
    store.record_result(run_id, "uniform_hourly", "peak_mae", b["baseline_peak_mae"], "C")
    for k, v in lat.items():
        store.record_result(run_id, "excursion_aware" if "raw_policy" not in k and "baseline" not in k else ("raw" if "raw_policy" in k else "uniform_hourly"), f"p95_{k}", v["p95_ms"], "ms", v)
    store.record_result(run_id, "excursion_aware", "ingest_readings_per_s", result["ingest"]["readings_per_s"], "readings/s")
    store.record_result(run_id, "excursion_aware", "late_data_bound", cfg.reconcile_max_wait_s, "s", late)
    store.conn.commit()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"benchmark-{run_id}.json").write_text(json.dumps(result, indent=2, default=str))
    (out / f"benchmark-{run_id}.md").write_text(_markdown(result))
    try:
        _plots(result, out / f"benchmark-{run_id}")
    except Exception as exc:  # plotting is a convenience, never a failure
        print(f"(plots skipped: {exc})")
    print(_markdown(result))
    store.close()
    qconn.close()
    return result


def _fmt_bytes(b: float | None) -> str:
    if b is None:
        return "–"
    for unit in ("B", "kB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _markdown(r: dict) -> str:
    rc = r["recall"]
    lat = r["latency"]
    lines = [
        f"# Benchmark {r['run_id']}",
        "",
        f"{r['shipments']} shipments, {r['readings']:,} readings, {rc['ground_truth']} injected excursions ≥ 60 s. "
        f"Ingest {r['ingest']['readings_per_s']:,.0f} readings/s (all three policies written in the same pass).",
        "",
        "| Metric | Raw | Uniform hourly | Excursion-aware |",
        "|---|---|---|---|",
        f"| Bytes stored | {_fmt_bytes(r['bytes']['raw'])} (100%) | {_fmt_bytes(r['bytes']['uniform_hourly'])} ({r['ratio']['uniform_hourly']:.2%}) | {_fmt_bytes(r['bytes']['excursion_aware'])} ({r['ratio']['excursion_aware']:.2%}) |",
    ]
    if r.get("bytes_compressed"):
        c = r["bytes_compressed"]; rc2 = r["ratio_compressed"]
        lines.append(f"| Bytes, TimescaleDB compressed | {_fmt_bytes(c['raw'])} (100%) | {_fmt_bytes(c['uniform_hourly'])} ({rc2['uniform_hourly']:.2%}) | {_fmt_bytes(c['excursion_aware'])} ({rc2['excursion_aware']:.2%}) |")
    lines += [
        f"| Excursion recall (start, end, peak within 10 s) | 100% | {rc['uniform_hourly_mean']:.1%} by hourly mean, {rc['uniform_hourly_minmax']:.1%} by hourly min/max (hour-level only) | {rc['excursion_aware']:.1%} |",
        f"| Peak error (MAE) | 0.00 °C | {rc['uniform_hourly_peak_mae']:.2f} °C (hourly mean vs true peak) | {rc['excursion_aware_peak_mae']:.4f} °C |",
        f"| p95 latency, scenario 1 (hidden excursions) | – | – | {lat['scenario1_hidden']['p95_ms']:.0f} ms |",
        f"| p95 latency, scenario 2 (excursion list + custody) | – | – | {lat['scenario2_custody_list']['p95_ms']:.0f} ms |",
        f"| p95 latency, scenario 2 (excursion detail, custody at start/peak/end) | – | – | {lat['scenario2_custody_detail']['p95_ms']:.0f} ms |",
        f"| p95 latency, scenario 3 (route risk ranking) | – | – | {lat['scenario3_route_risk']['p95_ms']:.0f} ms |",
        f"| p95 latency, full shipment trace | {lat['trace_raw_policy']['p95_ms']:.0f} ms | {lat['trace_baseline']['p95_ms']:.0f} ms | {lat['trace_full_shipment']['p95_ms']:.0f} ms |" if "trace_raw_policy" in lat else f"| p95 latency, full shipment trace | – | {lat['trace_baseline']['p95_ms']:.0f} ms | {lat['trace_full_shipment']['p95_ms']:.0f} ms |",
        f"| Late-data visibility bound | n/a | n/a | ≤ {r['late_data']['max_wait_s']} s after arrival ({r['late_data']['bound_s']} s quiet / {r['late_data']['max_wait_s']} s max); {r['late_data']['replays']} replays, {r['late_data']['rewrites']} rewrites logged, {r['late_data']['unreconstructable']} unreconstructable |",
        f"| Ingestion throughput | {r['ingest']['readings_per_s']:,.0f} readings/s (single pass, all policies) | same | same |",
        "",
    ]
    if rc["missed"]:
        lines.append(f"Missed by excursion-aware store: {rc['missed']}")
    return "\n".join(lines)


def _plots(r: dict, stem: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    policies = ["raw", "uniform_hourly", "excursion_aware"]
    labels = ["Raw", "Uniform hourly", "Excursion-aware"]
    ratio = [r["ratio"][p] for p in policies]
    recall = [1.0, r["recall"]["uniform_hourly_mean"], r["recall"]["excursion_aware"]]
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(ratio, recall, s=80, color=["#9aa0a6", "#e4572e", "#2a6f97"], zorder=3)
    for x, y, l in zip(ratio, recall, labels):
        ax.annotate(l, (x, y), textcoords="offset points", xytext=(8, -4), fontsize=9)
    ax.set_xscale("log")
    ax.set_xlabel("stored bytes / raw (log)")
    ax.set_ylabel("excursion recall")
    ax.set_ylim(-0.05, 1.08)
    ax.grid(True, which="both", alpha=0.25)
    ax.set_title("Storage vs recall")
    fig.tight_layout()
    fig.savefig(stem.with_suffix(".storage-recall.png"), dpi=150)
    plt.close(fig)
