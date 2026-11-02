"""Sensitivity runs (in memory, no database): excursion rate × buffer length ×
late fraction, each reporting storage ratio and recall, for the trade-off curve
in the report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from coldchain.benchmark.recall import recall_in_memory
from coldchain.compaction.baseline import RawStore, UniformHourly
from coldchain.compaction.engine import CompactionEngine
from coldchain.ingest.pipeline import run_pipeline
from coldchain.models import EngineConfig
from coldchain.simulator import SimConfig, Simulator
from coldchain.storage.memory import MemoryStore

GRID = {
    "excursion_rate": [0.005, 0.02, 0.10],
    "buffer_min": [0, 5, 10, 30],
    "late_fraction": [0.0, 0.05, 0.20],
}


def one(shipments: int, seed: int, excursion_rate: float, buffer_min: int, late_fraction: float) -> dict:
    sim = Simulator(
        SimConfig(seed=seed, n_shipments=shipments, excursion_rate=excursion_rate, late_fraction=late_fraction),
        EngineConfig(pre_buffer_s=buffer_min * 60, post_buffer_s=buffer_min * 60),
    )
    store = MemoryStore()
    res = run_pipeline(sim.events(), store, CompactionEngine(store, sim.engine_cfg), UniformHourly(store), RawStore(store))
    b = store.bytes()
    rec = recall_in_memory(sim, store)
    return {
        "excursion_rate": excursion_rate, "buffer_min": buffer_min, "late_fraction": late_fraction,
        "readings": res.readings, "readings_per_s": res.readings_per_s,
        "storage_ratio": b["excursion_aware_total"] / b["raw_store"], "baseline_ratio": b["baseline_hourly"] / b["raw_store"],
        "recall": rec["recall"], "baseline_mean_recall": rec["baseline_mean_recall"], "baseline_minmax_recall": rec["baseline_minmax_recall"],
        "replays": res.engine_stats["replays"], "rewrites": res.engine_stats["rewrites"], "unreconstructable": res.engine_stats["unreconstructable"],
    }


def run_sweep(shipments: int, seed: int, out: str) -> list[dict]:
    rows = []
    # vary one axis at a time around the PRD defaults (2 %, 10 min, 5 %)
    runs = (
        [(x, 10, 0.05) for x in GRID["excursion_rate"]]
        + [(0.02, bmin, 0.05) for bmin in GRID["buffer_min"]]
        + [(0.02, 10, lf) for lf in GRID["late_fraction"]]
    )
    seen = set()
    for x, bmin, lf in runs:
        if (x, bmin, lf) in seen:
            continue
        seen.add((x, bmin, lf))
        r = one(shipments, seed, x, bmin, lf)
        rows.append(r)
        print(f"rate={x:<6} buffer={bmin:>2}min late={lf:<5} → ratio {r['storage_ratio']:.2%}  recall {r['recall']:.1%}  baseline {r['baseline_mean_recall']:.1%}  replays {r['replays']}", file=sys.stderr)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rows, indent=2))
    try:
        _plot(rows, Path(out).with_suffix(".png"))
    except Exception as exc:
        print(f"(plot skipped: {exc})", file=sys.stderr)
    return rows


def _plot(rows: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    for ax, key, title in zip(axes, ("excursion_rate", "buffer_min", "late_fraction"), ("Excursion rate", "Buffer length (min)", "Late fraction")):
        fixed = {"excursion_rate": 0.02, "buffer_min": 10, "late_fraction": 0.05}
        sel = sorted([r for r in rows if all(r[k] == v for k, v in fixed.items() if k != key)], key=lambda r: r[key])
        xs = [r[key] for r in sel]
        ax.plot(xs, [r["storage_ratio"] for r in sel], marker="o", color="#2a6f97", label="storage ratio (ours)")
        ax.plot(xs, [r["baseline_ratio"] for r in sel], marker="s", color="#9aa0a6", label="storage ratio (hourly)")
        ax2 = ax.twinx()
        ax2.plot(xs, [r["recall"] for r in sel], marker="^", color="#1b998b", linestyle="--", label="recall (ours)")
        ax2.plot(xs, [r["baseline_mean_recall"] for r in sel], marker="v", color="#e4572e", linestyle="--", label="recall (hourly mean)")
        ax2.set_ylim(-0.05, 1.08)
        ax.set_title(title)
        ax.set_ylabel("stored / raw")
        ax.grid(alpha=0.25)
        if ax is axes[-1]:
            h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="center right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
