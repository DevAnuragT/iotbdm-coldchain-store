"""Query API (FastAPI). Thin: every endpoint is one function in `queries`.

Comparison mode (F13): trace and the scenario endpoints accept `store=` so the
same question can be asked of the excursion-aware store and of the uniform
hourly baseline.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from coldchain.api import queries
from coldchain.storage.postgres import dsn

pool: ConnectionPool | None = None
_demo_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = ConnectionPool(dsn(), min_size=2, max_size=8, kwargs={"row_factory": dict_row, "autocommit": True})
    pool.wait()
    yield
    pool.close()


app = FastAPI(title="Excursion-aware cold-chain store", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _conn():
    assert pool is not None
    return pool.connection()


@app.get("/api/health")
def health():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        return {"ok": bool(cur.fetchone()["ok"])}


@app.get("/api/overview")
def overview():
    with _conn() as conn:
        return queries.overview(conn)


@app.get("/api/shipments")
def shipments(
    q: str | None = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    only_excursions: bool = False,
    order: str = Query("start", pattern="^(start|excursions|peak)$"),
):
    with _conn() as conn:
        return queries.list_shipments(conn, q, limit, offset, only_excursions, order)


@app.get("/api/shipments/{shipment_id}")
def shipment(shipment_id: int):
    with _conn() as conn:
        d = queries.shipment_detail(conn, shipment_id)
    if d is None:
        raise HTTPException(404, "no such shipment")
    return d


@app.get("/api/shipments/{shipment_id}/trace")
def shipment_trace(
    shipment_id: int,
    from_ts: int | None = Query(None, alias="from"),
    to_ts: int | None = Query(None, alias="to"),
    store: str = Query("excursion_aware", pattern="^(excursion_aware|baseline|raw)$"),
):
    with _conn() as conn:
        return queries.trace(conn, shipment_id, from_ts, to_ts, store)


@app.get("/api/excursions")
def excursions(
    shipment_id: int | None = None,
    holder_type: str | None = None,
    holder_id: int | None = None,
    min_duration: int | None = Query(None, description="seconds"),
    min_peak_dev: float | None = Query(None, description="°C beyond the nearest threshold"),
    kind: str | None = Query(None, pattern="^(hot|cold)$"),
    order: str = Query("start", pattern="^(start|duration|peak)$"),
    limit: int = Query(50, le=500),
    offset: int = 0,
):
    with _conn() as conn:
        return queries.list_excursions(conn, shipment_id, holder_type, holder_id, min_duration, min_peak_dev, kind, order, limit, offset)


@app.get("/api/excursions/{excursion_id}")
def excursion(excursion_id: int):
    with _conn() as conn:
        d = queries.excursion_detail(conn, excursion_id)
    if d is None:
        raise HTTPException(404, "no such excursion")
    return d


@app.get("/api/scenarios/hidden")
def hidden(limit: int = Query(50, le=500), offset: int = 0):
    with _conn() as conn:
        return queries.hidden_excursions(conn, limit, offset)


@app.get("/api/scenarios/route-risk")
def route_risk(group: str = Query("holder", pattern="^(holder|type)$"), limit: int = Query(50, le=500)):
    with _conn() as conn:
        return queries.route_risk(conn, group, limit)


@app.get("/api/storage")
def storage(shipment_id: int | None = None):
    with _conn() as conn:
        return queries.storage(conn, shipment_id)


@app.get("/api/rewrites")
def rewrites(shipment_id: int | None = None, reason: str | None = None, limit: int = Query(50, le=500), offset: int = 0):
    with _conn() as conn:
        return queries.rewrites(conn, shipment_id, reason, limit, offset)


@app.get("/api/holders")
def holders(holder_type: str | None = None):
    with _conn() as conn:
        return queries.holders(conn, holder_type)


@app.post("/api/demo/late-data")
def demo_late_data(delay_hours: float = Query(3.0, ge=0.5, le=6.0)):
    """Scenario 5, live: a fresh shipment whose excursion arrives `delay_hours` late."""
    from coldchain.benchmark.demo import late_data_scenario

    if not _demo_lock.acquire(blocking=False):
        raise HTTPException(409, "a demo is already running")
    try:
        return late_data_scenario(delay_hours=delay_hours)
    finally:
        _demo_lock.release()
