"""SQL behind the query API (F11–F13). Each function takes a psycopg connection
with dict rows and returns JSON-ready data."""

from __future__ import annotations

from datetime import datetime, timezone

from psycopg import Connection

from coldchain.models import EngineConfig

CFG = EngineConfig()
HOUR = 3600


def _ts(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp())


def _dt(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


# --------------------------------------------------------------- overview
def overview(conn: Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
        run = cur.fetchone()
        cur.execute(
            """SELECT (SELECT count(*) FROM shipments) AS shipments,
                      (SELECT count(*) FROM excursions) AS excursions,
                      (SELECT count(*) FROM summary_windows) AS windows,
                      (SELECT count(*) FROM readings_raw) AS raw_rows,
                      (SELECT count(*) FROM rewrite_log) AS rewrites,
                      (SELECT count(*) FROM holders) AS holders,
                      (SELECT min(start_ts) FROM shipments) AS period_start,
                      (SELECT max(end_ts) FROM shipments) AS period_end"""
        )
        counts = cur.fetchone()
        cur.execute(
            """SELECT count(*) FILTER (WHERE kind='hot') AS hot, count(*) FILTER (WHERE kind='cold') AS cold,
                      coalesce(sum(duration_s),0) AS total_s, coalesce(max(duration_s),0) AS longest_s,
                      coalesce(max(peak_c),0) AS hottest, coalesce(min(peak_c),0) AS coldest
               FROM excursions"""
        )
        ex = cur.fetchone()
    return {
        "run": {
            "run_id": run["run_id"] if run else None,
            "seed": run["seed"] if run else None,
            "readings": run["readings"] if run else None,
            "seconds": run["seconds"] if run else None,
            "stats": run["stats"] if run else None,
            "config": run["config"] if run else None,
        } if run else None,
        "counts": {k: (int(v) if isinstance(v, (int, float)) and k not in ("period_start", "period_end") else v) for k, v in counts.items()},
        "excursions": dict(ex),
        "storage": storage(conn),
    }


# --------------------------------------------------------------- shipments
def list_shipments(conn: Connection, q: str | None, limit: int, offset: int, only_excursions: bool, order: str) -> dict:
    order_sql = {
        "start": "s.start_ts DESC",
        "excursions": "n_excursions DESC, s.start_ts DESC",
        "peak": "worst_dev DESC NULLS LAST",
    }.get(order, "s.start_ts DESC")
    where = []
    params: list = []
    if q:
        if q.isdigit():
            where.append("s.shipment_id = %s")
            params.append(int(q))
        else:
            where.append("(s.product ILIKE %s OR ho.name ILIKE %s OR hd.name ILIKE %s)")
            params += [f"%{q}%"] * 3
    if only_excursions:
        where.append("EXISTS (SELECT 1 FROM excursions e WHERE e.shipment_id = s.shipment_id)")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT s.shipment_id, s.product, s.start_ts, s.end_ts,
                       ho.name AS origin, hd.name AS destination, hd.region AS region,
                       x.n_excursions, x.worst_dev, x.peak_c, x.excursion_s,
                       count(*) OVER () AS total
                FROM shipments s
                JOIN holders ho ON ho.holder_id = s.origin_id
                JOIN holders hd ON hd.holder_id = s.destination_id
                LEFT JOIN LATERAL (
                    SELECT count(*) AS n_excursions,
                           max(greatest(peak_c - %s, %s - peak_c)) AS worst_dev,
                           (array_agg(peak_c ORDER BY greatest(peak_c - %s, %s - peak_c) DESC))[1] AS peak_c,
                           coalesce(sum(duration_s),0) AS excursion_s
                    FROM excursions e WHERE e.shipment_id = s.shipment_id
                ) x ON true
                {where_sql}
                ORDER BY {order_sql}
                LIMIT %s OFFSET %s""",
            [CFG.hi_enter_c, CFG.lo_enter_c, CFG.hi_enter_c, CFG.lo_enter_c, *params, limit, offset],
        )
        rows = cur.fetchall()
    total = int(rows[0]["total"]) if rows else 0
    items = []
    for r in rows:
        items.append({
            "shipment_id": r["shipment_id"], "product": r["product"],
            "start_ts": _ts(r["start_ts"]), "end_ts": _ts(r["end_ts"]),
            "origin": r["origin"], "destination": r["destination"], "region": r["region"],
            "n_excursions": int(r["n_excursions"] or 0), "peak_c": r["peak_c"], "excursion_s": int(r["excursion_s"] or 0),
        })
    return {"total": total, "items": items}


def shipment_detail(conn: Connection, shipment_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.*, ho.name AS origin, hd.name AS destination FROM shipments s
               JOIN holders ho ON ho.holder_id = s.origin_id JOIN holders hd ON hd.holder_id = s.destination_id
               WHERE s.shipment_id = %s""",
            (shipment_id,),
        )
        s = cur.fetchone()
        if s is None:
            return None
        cur.execute(
            """SELECT c.holder_id, c.holder_type, h.name, h.region, c.valid_from, c.valid_to
               FROM custody c JOIN holders h USING (holder_id) WHERE c.shipment_id = %s ORDER BY c.valid_from""",
            (shipment_id,),
        )
        custody = [{**r, "valid_from": _ts(r["valid_from"]), "valid_to": _ts(r["valid_to"])} for r in cur.fetchall()]
        cur.execute("SELECT * FROM excursions WHERE shipment_id = %s ORDER BY start_ts", (shipment_id,))
        excs = [_exc_row(r) for r in cur.fetchall()]
        cur.execute(
            """SELECT count(*) AS windows, coalesce(sum(n),0) AS summarised, max(version) AS max_version
               FROM summary_windows WHERE shipment_id = %s""",
            (shipment_id,),
        )
        w = cur.fetchone()
        cur.execute("SELECT count(*) AS raw_rows FROM readings_raw WHERE shipment_id = %s", (shipment_id,))
        raw = cur.fetchone()["raw_rows"]
        cur.execute("SELECT count(*) AS rewrites FROM rewrite_log WHERE shipment_id = %s", (shipment_id,))
        rw = cur.fetchone()["rewrites"]
    n_readings = (s["end_ts"].timestamp() - s["start_ts"].timestamp()) / 10
    return {
        "shipment_id": s["shipment_id"], "product": s["product"], "origin": s["origin"], "destination": s["destination"],
        "start_ts": _ts(s["start_ts"]), "end_ts": _ts(s["end_ts"]),
        "custody": custody, "excursions": excs,
        "storage": {
            "readings": int(n_readings), "windows": int(w["windows"]), "summarised": int(w["summarised"]),
            "raw_rows": int(raw), "rewrites": int(rw), "max_version": int(w["max_version"] or 1),
        },
    }


def trace(conn: Connection, shipment_id: int, from_ts: int | None, to_ts: int | None, store: str) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT start_ts, end_ts FROM shipments WHERE shipment_id = %s", (shipment_id,))
        s = cur.fetchone()
        if s is None:
            return {"shipment_id": shipment_id, "windows": [], "raw": [], "hourly": []}
        lo = _dt(from_ts) if from_ts else s["start_ts"]
        hi = _dt(to_ts) if to_ts else s["end_ts"]
        out: dict = {"shipment_id": shipment_id, "from_ts": _ts(lo), "to_ts": _ts(hi), "store": store, "windows": [], "raw": [], "hourly": []}
        if store == "baseline":
            cur.execute(
                "SELECT hour, n, mean_c, min_c, max_c FROM baseline_hourly WHERE shipment_id=%s AND hour >= date_trunc('hour', %s::timestamptz) AND hour <= %s ORDER BY hour",
                (shipment_id, lo, hi),
            )
            out["hourly"] = [{"ts": _ts(r["hour"]), "n": r["n"], "mean": r["mean_c"], "min": r["min_c"], "max": r["max_c"]} for r in cur.fetchall()]
            return out
        if store == "raw":
            cur.execute(
                "SELECT ts, temp_c FROM readings_rawstore WHERE shipment_id=%s AND ts >= %s AND ts <= %s ORDER BY ts",
                (shipment_id, lo, hi),
            )
            out["raw"] = [{"ts": _ts(r["ts"]), "temp": r["temp_c"], "excursion_id": None} for r in cur.fetchall()]
            return out
        cur.execute(
            """SELECT window_id, window_start, window_end, n, min_c, max_c, mean_c, sd_c, last_c, version
               FROM summary_windows WHERE shipment_id=%s AND window_end >= %s AND window_start <= %s ORDER BY window_start""",
            (shipment_id, lo, hi),
        )
        out["windows"] = [
            {"window_id": r["window_id"], "start": _ts(r["window_start"]), "end": _ts(r["window_end"]), "n": r["n"],
             "min": r["min_c"], "max": r["max_c"], "mean": r["mean_c"], "sd": r["sd_c"], "last": r["last_c"], "version": r["version"]}
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT ts, temp_c, humidity_pct, excursion_id FROM readings_raw WHERE shipment_id=%s AND ts >= %s AND ts <= %s ORDER BY ts",
            (shipment_id, lo, hi),
        )
        out["raw"] = [{"ts": _ts(r["ts"]), "temp": r["temp_c"], "humidity": r["humidity_pct"], "excursion_id": r["excursion_id"]} for r in cur.fetchall()]
    return out


# -------------------------------------------------------------- excursions
def _exc_row(r) -> dict:
    return {
        "excursion_id": r["excursion_id"], "shipment_id": r["shipment_id"],
        "start_ts": _ts(r["start_ts"]), "end_ts": _ts(r["end_ts"]), "peak_c": r["peak_c"],
        "duration_s": r["duration_s"], "detected_at": _ts(r["detected_at"]), "kind": r["kind"],
        **({"holder": r["holder_name"], "holder_type": r["holder_type"], "holder_id": r["holder_id"], "region": r["holder_region"]} if "holder_name" in r else {}),
        **({"product": r["product"]} if "product" in r else {}),
    }


EXCURSION_SELECT = """
    SELECT e.*, s.product, h.holder_id, h.holder_type, h.name AS holder_name, h.region AS holder_region, count(*) OVER () AS total
    FROM excursions e
    JOIN shipments s USING (shipment_id)
    LEFT JOIN LATERAL holder_at(e.shipment_id, e.start_ts) h ON true
"""


def list_excursions(
    conn: Connection, shipment_id: int | None, holder_type: str | None, holder_id: int | None,
    min_duration: int | None, min_peak_dev: float | None, kind: str | None, order: str, limit: int, offset: int,
) -> dict:
    where = []
    params: list = []
    if shipment_id is not None:
        where.append("e.shipment_id = %s"); params.append(shipment_id)
    if holder_type:
        where.append("h.holder_type = %s"); params.append(holder_type)
    if holder_id is not None:
        where.append("h.holder_id = %s"); params.append(holder_id)
    if min_duration:
        where.append("e.duration_s >= %s"); params.append(min_duration)
    if min_peak_dev:
        where.append("greatest(e.peak_c - %s, %s - e.peak_c) >= %s"); params += [CFG.hi_enter_c, CFG.lo_enter_c, min_peak_dev]
    if kind:
        where.append("e.kind = %s"); params.append(kind)
    order_sql = {
        "start": "e.start_ts DESC",
        "duration": "e.duration_s DESC",
        "peak": f"greatest(e.peak_c - {CFG.hi_enter_c}, {CFG.lo_enter_c} - e.peak_c) DESC",
    }.get(order, "e.start_ts DESC")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(f"{EXCURSION_SELECT} {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s", [*params, limit, offset])
        rows = cur.fetchall()
    return {"total": int(rows[0]["total"]) if rows else 0, "items": [_exc_row(r) for r in rows]}


def excursion_detail(conn: Connection, excursion_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT e.*, s.product FROM excursions e JOIN shipments s USING (shipment_id) WHERE excursion_id = %s", (excursion_id,))
        e = cur.fetchone()
        if e is None:
            return None
        cur.execute(
            "SELECT ts, temp_c, humidity_pct, excursion_id FROM readings_raw WHERE shipment_id=%s AND excursion_id=%s ORDER BY ts",
            (e["shipment_id"], excursion_id),
        )
        raw = [{"ts": _ts(r["ts"]), "temp": r["temp_c"], "humidity": r["humidity_pct"]} for r in cur.fetchall()]
        peak_ts = None
        if raw:
            peak_ts = max(raw, key=lambda p: max(p["temp"] - CFG.hi_enter_c, CFG.lo_enter_c - p["temp"]))["ts"]
        custody = {}
        for label, ts in (("start", _ts(e["start_ts"])), ("peak", peak_ts or _ts(e["start_ts"])), ("end", _ts(e["end_ts"]))):
            cur.execute("SELECT * FROM holder_at(%s, %s)", (e["shipment_id"], _dt(ts)))
            h = cur.fetchone()
            custody[label] = {**h, "valid_from": _ts(h["valid_from"]), "valid_to": _ts(h["valid_to"]), "at_ts": ts} if h else None
        cur.execute(
            "SELECT hour, n, mean_c, min_c, max_c FROM baseline_hourly WHERE shipment_id=%s AND hour >= date_trunc('hour', %s::timestamptz) AND hour <= %s ORDER BY hour",
            (e["shipment_id"], e["start_ts"], e["end_ts"]),
        )
        hourly = [{"ts": _ts(r["hour"]), "n": r["n"], "mean": r["mean_c"], "min": r["min_c"], "max": r["max_c"]} for r in cur.fetchall()]
        cur.execute(
            """SELECT * FROM rewrite_log WHERE shipment_id=%s AND late_reading_ts BETWEEN %s - interval '12 hours' AND %s + interval '12 hours'
               ORDER BY rewritten_at""",
            (e["shipment_id"], e["start_ts"], e["end_ts"]),
        )
        rewrites = [_rewrite_row(r) for r in cur.fetchall()]
    hidden = all(CFG.lo_enter_c <= h["mean"] <= CFG.hi_enter_c for h in hourly) if hourly else False
    return {**_exc_row(e), "peak_ts": peak_ts, "custody": custody, "raw": raw, "hourly": hourly, "hidden_from_hourly_mean": hidden, "rewrites": rewrites}


# --------------------------------------------------------------- scenarios
def hidden_excursions(conn: Connection, limit: int, offset: int) -> dict:
    """Scenario 1: excursions invisible to an hourly-mean store."""
    with conn.cursor() as cur:
        cur.execute(
            f"""WITH hidden AS (
                    SELECT e.excursion_id FROM excursions e
                    WHERE NOT EXISTS (
                        SELECT 1 FROM baseline_hourly b
                        WHERE b.shipment_id = e.shipment_id
                          AND b.hour >= date_trunc('hour', e.start_ts) AND b.hour <= e.end_ts
                          AND (b.mean_c > %s OR b.mean_c < %s))
                ),
                visible_minmax AS (
                    SELECT e.excursion_id FROM excursions e
                    WHERE EXISTS (
                        SELECT 1 FROM baseline_hourly b
                        WHERE b.shipment_id = e.shipment_id
                          AND b.hour >= date_trunc('hour', e.start_ts) AND b.hour <= e.end_ts
                          AND (b.max_c > %s OR b.min_c < %s))
                )
                SELECT (SELECT count(*) FROM excursions) AS total,
                       (SELECT count(*) FROM hidden) AS hidden,
                       (SELECT count(*) FROM visible_minmax) AS visible_minmax,
                       (SELECT count(DISTINCT shipment_id) FROM excursions e JOIN hidden USING (excursion_id)) AS hidden_shipments""",
            (CFG.hi_enter_c, CFG.lo_enter_c, CFG.hi_enter_c, CFG.lo_enter_c),
        )
        summary = dict(cur.fetchone())
        cur.execute(
            f"""{EXCURSION_SELECT}
                WHERE NOT EXISTS (
                    SELECT 1 FROM baseline_hourly b
                    WHERE b.shipment_id = e.shipment_id
                      AND b.hour >= date_trunc('hour', e.start_ts) AND b.hour <= e.end_ts
                      AND (b.mean_c > %s OR b.mean_c < %s))
                ORDER BY greatest(e.peak_c - %s, %s - e.peak_c) * e.duration_s DESC
                LIMIT %s OFFSET %s""",
            (CFG.hi_enter_c, CFG.lo_enter_c, CFG.hi_enter_c, CFG.lo_enter_c, limit, offset),
        )
        rows = cur.fetchall()
    summary = {k: int(v) for k, v in summary.items()}
    summary["baseline_mean_recall"] = (summary["total"] - summary["hidden"]) / summary["total"] if summary["total"] else None
    summary["baseline_minmax_recall"] = summary["visible_minmax"] / summary["total"] if summary["total"] else None
    return {"summary": summary, "items": [_exc_row(r) for r in rows]}


def route_risk(conn: Connection, group: str, limit: int) -> list[dict]:
    """Scenario 3: rank legs (holders, or holder types) by excursion-minutes per shipment."""
    key = "h.holder_id, h.name, h.type, h.region" if group == "holder" else "h.type"
    sel = "h.holder_id, h.name, h.type, h.region" if group == "holder" else "NULL::int AS holder_id, h.type AS name, h.type, NULL::text AS region"
    with conn.cursor() as cur:
        cur.execute(
            f"""WITH legs AS (
                    -- one row per custody leg: minutes of overlap with excursions
                    SELECT c.holder_id, c.shipment_id, c.valid_from, c.valid_to,
                           count(e.excursion_id) AS n_exc,
                           coalesce(sum(CASE WHEN e.excursion_id IS NOT NULL THEN
                               extract(epoch FROM (least(e.end_ts, c.valid_to) - greatest(e.start_ts, c.valid_from))) END) / 60, 0) AS exc_min
                    FROM custody c
                    LEFT JOIN excursions e ON e.shipment_id = c.shipment_id
                         AND tstzrange(c.valid_from, c.valid_to, '[)') && tstzrange(e.start_ts, e.end_ts, '[]')
                    GROUP BY 1, 2, 3, 4
                )
                SELECT {sel},
                       count(DISTINCT l.shipment_id) AS shipments,
                       sum(l.n_exc) AS excursions,
                       sum(l.exc_min) AS excursion_minutes,
                       sum(extract(epoch FROM (l.valid_to - l.valid_from))) / 3600 AS custody_hours
                FROM legs l JOIN holders h USING (holder_id)
                GROUP BY {key}
                ORDER BY sum(l.exc_min) / NULLIF(count(DISTINCT l.shipment_id), 0) DESC NULLS LAST
                LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        ships = int(r["shipments"])
        out.append({
            "holder_id": r["holder_id"], "name": r["name"], "type": r["type"], "region": r["region"],
            "shipments": ships, "excursions": int(r["excursions"]),
            "excursion_minutes": float(r["excursion_minutes"]), "custody_hours": float(r["custody_hours"]),
            "minutes_per_shipment": float(r["excursion_minutes"]) / ships if ships else 0.0,
        })
    return out


def storage(conn: Connection, shipment_id: int | None = None) -> dict:
    """Scenario 4: bytes per policy, with recall from the latest benchmark if present."""
    hyper = ["readings_raw", "summary_windows", "baseline_hourly", "readings_rawstore"]
    plain = ["excursions", "rewrite_log", "custody"]
    sizes: dict[str, int] = {}
    rows: dict[str, int] = {}
    with conn.cursor() as cur:
        for t in hyper:
            cur.execute("SELECT hypertable_size(%s) AS b", (t,))
            sizes[t] = int(cur.fetchone()["b"] or 0)
        for t in plain:
            cur.execute("SELECT pg_total_relation_size(%s) AS b", (t,))
            sizes[t] = int(cur.fetchone()["b"] or 0)
        for t in hyper + plain:
            cur.execute(f"SELECT count(*) AS n FROM {t}")
            rows[t] = int(cur.fetchone()["n"])
        cur.execute("SELECT run_id, policy, metric, value, unit, detail FROM benchmark_results WHERE run_id = (SELECT run_id FROM benchmark_results ORDER BY recorded_at DESC LIMIT 1)")
        bench_rows = cur.fetchall()
        per_shipment = None
        if shipment_id is not None:
            cur.execute("SELECT count(*) AS n FROM readings_raw WHERE shipment_id=%s", (shipment_id,)); n_raw = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM summary_windows WHERE shipment_id=%s", (shipment_id,)); n_win = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM excursions WHERE shipment_id=%s", (shipment_id,)); n_exc = cur.fetchone()["n"]
            cur.execute("SELECT count(*) AS n FROM baseline_hourly WHERE shipment_id=%s", (shipment_id,)); n_base = cur.fetchone()["n"]
            cur.execute("SELECT extract(epoch FROM end_ts - start_ts)/10 AS n FROM shipments WHERE shipment_id=%s", (shipment_id,))
            r = cur.fetchone(); n_all = int(r["n"]) if r else 0

            def width(t: str) -> float:
                return sizes[t] / rows[t] if rows[t] else 0.0

            per_shipment = {
                "readings": n_all,
                "raw": n_all * (width("readings_rawstore") or width("readings_raw")),
                "uniform_hourly": n_base * width("baseline_hourly"),
                "excursion_aware": n_raw * width("readings_raw") + n_win * width("summary_windows") + n_exc * width("excursions"),
                "rows": {"raw": n_all, "uniform_hourly": n_base, "excursion_aware_raw": n_raw, "windows": n_win, "excursions": n_exc},
            }
    policies = {
        "raw": sizes["readings_rawstore"],
        "uniform_hourly": sizes["baseline_hourly"],
        "excursion_aware": sizes["readings_raw"] + sizes["summary_windows"] + sizes["excursions"],
        "audit_trail": sizes["rewrite_log"],
    }
    bench: dict[str, dict] = {}
    for r in bench_rows:
        bench.setdefault(r["policy"], {})[r["metric"]] = {"value": r["value"], "unit": r["unit"], "detail": r["detail"]}
    return {
        "tables": {t: {"bytes": sizes[t], "rows": rows[t]} for t in sizes},
        "policies": policies,
        "ratio": {k: (v / policies["raw"] if policies["raw"] else None) for k, v in policies.items()},
        "benchmark": bench,
        "benchmark_run": bench_rows[0]["run_id"] if bench_rows else None,
        "per_shipment": per_shipment,
    }


def _rewrite_row(r) -> dict:
    return {
        "id": r["id"], "window_id": r["window_id"], "shipment_id": r["shipment_id"],
        "old_version": r["old_version"], "new_version": r["new_version"], "reason": r["reason"],
        "late_reading_ts": _ts(r["late_reading_ts"]), "rewritten_at": _ts(r["rewritten_at"]), "detail": r["detail"],
    }


def rewrites(conn: Connection, shipment_id: int | None, reason: str | None, limit: int, offset: int) -> dict:
    where = []
    params: list = []
    if shipment_id is not None:
        where.append("shipment_id = %s"); params.append(shipment_id)
    if reason:
        where.append("reason = %s"); params.append(reason)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute(f"SELECT *, count(*) OVER () AS total FROM rewrite_log {where_sql} ORDER BY rewritten_at DESC, id DESC LIMIT %s OFFSET %s", [*params, limit, offset])
        rows = cur.fetchall()
        cur.execute("SELECT reason, count(*) AS n FROM rewrite_log GROUP BY reason ORDER BY n DESC")
        by_reason = {r["reason"]: int(r["n"]) for r in cur.fetchall()}
    return {"total": int(rows[0]["total"]) if rows else 0, "by_reason": by_reason, "items": [_rewrite_row(r) for r in rows]}


def holders(conn: Connection, holder_type: str | None) -> list[dict]:
    with conn.cursor() as cur:
        if holder_type:
            cur.execute("SELECT * FROM holders WHERE type = %s ORDER BY name", (holder_type,))
        else:
            cur.execute("SELECT * FROM holders ORDER BY type, name")
        return [dict(r) for r in cur.fetchall()]
