"""TimescaleDB store.

Inserts are buffered and written with COPY; updates and deletes are rare (late
data only) and run immediately after draining the buffers so they see every
row the engine has emitted so far.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime, timezone

import psycopg
from psycopg import sql

from coldchain.models import (
    CustodyInterval,
    Excursion,
    Holder,
    RawRow,
    Reading,
    RewriteEntry,
    Shipment,
    SummaryWindow,
)

DEFAULT_DSN = "postgresql://coldchain:coldchain@localhost:5432/coldchain"


def dsn() -> str:
    return os.environ.get("COLDCHAIN_DSN", DEFAULT_DSN)


def _dt(ts: int | None) -> datetime | None:
    return None if ts is None else datetime.fromtimestamp(ts, tz=timezone.utc)


class PostgresStore:
    def __init__(self, conn: psycopg.Connection | None = None, batch: int = 50_000):
        self.conn = conn or psycopg.connect(dsn(), autocommit=False)
        self.batch = batch
        self._windows: list[SummaryWindow] = []
        self._excursions: list[Excursion] = []
        self._raw: list[RawRow] = []
        self._rewrites: list[RewriteEntry] = []
        self._baseline: list[tuple] = []
        self._rawstore: list[Reading] = []
        self._shipments: list[Shipment] = []
        self._custody: list[CustodyInterval] = []

    # ------------------------------------------------------------ dimensions
    def write_holders(self, holders: Iterable[Holder]) -> None:
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO holders (holder_id, name, type, region) VALUES (%s, %s, %s, %s) ON CONFLICT (holder_id) DO NOTHING",
                [(h.holder_id, h.name, h.type.value, h.region) for h in holders],
            )
        self.conn.commit()

    def shipment_started(self, shipment, custody):
        self._shipments.append(shipment)
        self._custody.extend(custody)
        if len(self._shipments) >= 200:
            self._flush_dimensions()

    def shipment_finished(self, shipment_id):
        pass

    def _flush_dimensions(self) -> None:
        if not self._shipments:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY shipments (shipment_id, origin_id, destination_id, start_ts, end_ts, product) FROM STDIN") as cp:
                for s in self._shipments:
                    cp.write_row((s.shipment_id, s.origin_id, s.destination_id, _dt(s.start_ts), _dt(s.end_ts), s.product))
            with cur.copy("COPY custody (shipment_id, holder_id, holder_type, valid_from, valid_to) FROM STDIN") as cp:
                for c in self._custody:
                    cp.write_row((c.shipment_id, c.holder_id, c.holder_type.value, _dt(c.valid_from), _dt(c.valid_to)))
        self._shipments.clear()
        self._custody.clear()

    # ------------------------------------------------------------- artifacts
    def write_window(self, w):
        self._windows.append(w)
        if len(self._windows) >= self.batch:
            self._flush_windows()

    def update_window(self, w):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE summary_windows SET window_start=%s, window_end=%s, n=%s, min_c=%s, max_c=%s,
                   mean_c=%s, sd_c=%s, last_c=%s, version=%s WHERE shipment_id=%s AND window_id=%s""",
                (_dt(w.window_start), _dt(w.window_end), w.n, w.min_c, w.max_c, w.mean_c, w.sd_c, w.last_c,
                 w.version, w.shipment_id, w.window_id),
            )

    def delete_window(self, shipment_id, window_id):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM summary_windows WHERE shipment_id=%s AND window_id=%s", (shipment_id, window_id))

    def write_excursion(self, e):
        self._excursions.append(e)

    def update_excursion(self, e):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE excursions SET start_ts=%s, end_ts=%s, peak_c=%s, duration_s=%s WHERE excursion_id=%s",
                (_dt(e.start_ts), _dt(e.end_ts), e.peak_c, e.duration_s, e.excursion_id),
            )

    def delete_excursion(self, shipment_id, excursion_id):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM excursions WHERE excursion_id=%s", (excursion_id,))

    def write_raw(self, rows):
        self._raw.extend(rows)
        if len(self._raw) >= self.batch:
            self._flush_raw()

    def delete_raw(self, shipment_id, from_ts):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute("DELETE FROM readings_raw WHERE shipment_id=%s AND ts >= %s", (shipment_id, _dt(from_ts)))

    def write_rewrite(self, entry):
        self._rewrites.append(entry)

    def find_window(self, shipment_id, ts):
        self._drain()
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT window_id, shipment_id, window_start, window_end, n, min_c, max_c, mean_c, sd_c, last_c, version
                   FROM summary_windows WHERE shipment_id=%s AND window_start <= %s AND window_end >= %s LIMIT 1""",
                (shipment_id, _dt(ts), _dt(ts)),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return SummaryWindow(row[0], row[1], int(row[2].timestamp()), int(row[3].timestamp()), *row[4:])

    # ------------------------------------------------------ comparison stores
    def write_baseline(self, shipment_id, hour_ts, n, mean_c, min_c, max_c):
        self._baseline.append((shipment_id, _dt(hour_ts), n, mean_c, min_c, max_c))
        if len(self._baseline) >= self.batch:
            self._flush_baseline()

    def write_rawstore(self, r):
        self._rawstore.append(r)
        if len(self._rawstore) >= self.batch:
            self._flush_rawstore()

    # ------------------------------------------------------------- benchmark
    def write_ground_truth(self, rows: Iterable[tuple[int, int, int, float, int]]) -> None:
        with self.conn.cursor() as cur:
            with cur.copy("COPY ground_truth (shipment_id, start_ts, end_ts, peak_c, duration_s) FROM STDIN") as cp:
                for sid, a, b, peak, d in rows:
                    cp.write_row((sid, _dt(a), _dt(b), peak, d))

    def start_run(self, run_id: str, seed: int, n_shipments: int, config: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (run_id, seed, n_shipments, config, started_at) VALUES (%s, %s, %s, %s, now()) "
                "ON CONFLICT (run_id) DO UPDATE SET seed=EXCLUDED.seed, n_shipments=EXCLUDED.n_shipments, config=EXCLUDED.config, started_at=now(), finished_at=NULL",
                (run_id, seed, n_shipments, json.dumps(config)),
            )
        self.conn.commit()

    def finish_run(self, run_id: str, readings: int, seconds: float, stats: dict) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET finished_at=now(), readings=%s, seconds=%s, stats=%s WHERE run_id=%s",
                (readings, seconds, json.dumps(stats), run_id),
            )
        self.conn.commit()

    def record_result(self, run_id: str, policy: str, metric: str, value: float | None, unit: str, detail: dict | None = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO benchmark_results (run_id, policy, metric, value, unit, detail) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id, policy, metric) DO UPDATE SET value=EXCLUDED.value, unit=EXCLUDED.unit, detail=EXCLUDED.detail, recorded_at=now()",
                (run_id, policy, metric, value, unit, json.dumps(detail) if detail is not None else None),
            )
        self.conn.commit()

    def truncate_all(self) -> None:
        tables = ["readings_raw", "summary_windows", "excursions", "rewrite_log", "baseline_hourly",
                  "readings_rawstore", "ground_truth", "custody", "shipments", "holders", "benchmark_results", "runs"]
        with self.conn.cursor() as cur:
            cur.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(map(sql.Identifier, tables))))
        self.conn.commit()

    def table_bytes(self) -> dict[str, int]:
        hyper = ["readings_raw", "summary_windows", "baseline_hourly", "readings_rawstore"]
        plain = ["excursions", "custody", "rewrite_log", "shipments", "holders"]
        out: dict[str, int] = {}
        with self.conn.cursor() as cur:
            for t in hyper:
                cur.execute("SELECT hypertable_size(%s)", (t,))
                out[t] = int(cur.fetchone()[0] or 0)
            for t in plain:
                cur.execute("SELECT pg_total_relation_size(%s)", (t,))
                out[t] = int(cur.fetchone()[0] or 0)
        return out

    # ---------------------------------------------------------------- flush
    def _flush_windows(self) -> None:
        if not self._windows:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY summary_windows (window_id, shipment_id, window_start, window_end, n, min_c, max_c, mean_c, sd_c, last_c, version) FROM STDIN") as cp:
                for w in self._windows:
                    cp.write_row((w.window_id, w.shipment_id, _dt(w.window_start), _dt(w.window_end), w.n, w.min_c, w.max_c, w.mean_c, w.sd_c, w.last_c, w.version))
        self._windows.clear()

    def _flush_excursions(self) -> None:
        if not self._excursions:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY excursions (excursion_id, shipment_id, start_ts, end_ts, peak_c, duration_s, detected_at, kind) FROM STDIN") as cp:
                for e in self._excursions:
                    cp.write_row((e.excursion_id, e.shipment_id, _dt(e.start_ts), _dt(e.end_ts), e.peak_c, e.duration_s, _dt(e.detected_at), e.kind))
        self._excursions.clear()

    def _flush_raw(self) -> None:
        if not self._raw:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY readings_raw (shipment_id, ts, temp_c, humidity_pct, lat, lon, excursion_id) FROM STDIN") as cp:
                for r in self._raw:
                    cp.write_row((r.shipment_id, _dt(r.ts), r.temp_c, r.humidity_pct, r.lat, r.lon, r.excursion_id))
        self._raw.clear()

    def _flush_rewrites(self) -> None:
        if not self._rewrites:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY rewrite_log (window_id, shipment_id, old_version, new_version, reason, late_reading_ts, rewritten_at, detail) FROM STDIN") as cp:
                for e in self._rewrites:
                    cp.write_row((e.window_id, e.shipment_id, e.old_version, e.new_version, e.reason, _dt(e.late_reading_ts), _dt(e.rewritten_at), e.detail))
        self._rewrites.clear()

    def _flush_baseline(self) -> None:
        if not self._baseline:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY baseline_hourly (shipment_id, hour, n, mean_c, min_c, max_c) FROM STDIN") as cp:
                for row in self._baseline:
                    cp.write_row(row)
        self._baseline.clear()

    def _flush_rawstore(self) -> None:
        if not self._rawstore:
            return
        with self.conn.cursor() as cur:
            with cur.copy("COPY readings_rawstore (shipment_id, ts, temp_c, humidity_pct, lat, lon) FROM STDIN") as cp:
                for r in self._rawstore:
                    cp.write_row((r.shipment_id, _dt(r.ts), r.temp_c, r.humidity_pct, r.lat, r.lon))
        self._rawstore.clear()

    def _drain(self) -> None:
        """Write buffered inserts so that a following update/delete sees them."""
        self._flush_dimensions()
        self._flush_windows()
        self._flush_excursions()
        self._flush_raw()
        self._flush_rewrites()

    def flush(self) -> None:
        self._drain()
        self._flush_baseline()
        self._flush_rawstore()
        self.conn.commit()

    def close(self) -> None:
        self.flush()
        self.conn.close()
