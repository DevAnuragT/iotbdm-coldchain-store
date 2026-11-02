# Excursion-aware cold-chain data store

IoTBDM course project. A software-only data platform for vaccine cold-chain
telemetry that stores a year of 10-second readings at **6 % of raw size** while
guaranteeing that **no temperature excursion is lost**: steady in-range periods
collapse into summary windows, every reading around an out-of-range event is
kept at full resolution with a 10-minute buffer either side, a temporal custody
join says *who held the shipment* at the moment of any excursion, and an audit
trail records every rewrite caused by late-arriving data.

- [PRD.md](PRD.md) — what we are building and why (requirements F1–F15, N1–N7,
  evaluation plan, risks).
- [DEMO.md](DEMO.md) — what to show and say in the live demo, with fallbacks.
- `reports/` — benchmark tables and plots from the current dataset.

## Run it in 10 minutes

You need Docker Desktop, Python 3.11 (`uv` makes this painless) and Node 20 if
you want to hack on the dashboard.

```
git clone <this repo> && cd IoTBDM
docker compose up -d                          # TimescaleDB + API (:8000) + dashboard (:3100)
docker compose --profile load run loader      # 500 shipments, ~3 min; SHIPMENTS=5000 for the full run

# local Python, for the CLI, tests and benchmark
uv venv -p 3.11 && uv pip install -e ".[dev]"
source .venv/bin/activate
coldchain bench --compress                    # bytes, recall, p95 latency → reports/
```

Then open http://localhost:3100. Without Docker at all:

```
coldchain simulate --shipments 100            # engine in memory: ratio, recall, late-data stats, 10 s
coldchain sweep                               # sensitivity runs → reports/sensitivity.{json,png}
pytest                                        # 24 tests
```

Dashboard development with hot reload: `cd dashboard && npm install && npm run dev -- --port 5173`
(proxies `/api` to localhost:8000; run `coldchain api` locally or keep the
compose `api` container up).

## What is where

```
PRD.md, DEMO.md            requirements; demo script
db/init.sql                TimescaleDB schema: hypertables, custody range index, holder_at()
src/coldchain/
  models.py                records shared by every layer (integer epoch-second timestamps)
  simulator/               world (holders, routes), telemetry (drift, noise, injected
                           excursions, sub-minute blips), stream (arrival order, bursty late data)
  compaction/
    detector.py            THE definition of an excursion; reference detector = ground truth
    engine.py              the excursion-aware compaction engine — the graded novelty
    baseline.py            uniform-hourly and store-everything comparison policies
  ingest/pipeline.py       stream → validation → every policy; throughput measurement
  ingest/kafka.py          optional Redpanda transport (same events, keyed by shipment)
  storage/                 Store protocol; in-memory store (tests); TimescaleDB store (COPY batches)
  api/                     FastAPI: traces, excursions + custody, scenarios 1–4, storage audit, rewrites
  benchmark/               recall matching, benchmark runner, sensitivity sweep, scenario-5 demo
  cli.py                   `coldchain simulate | load | bench | sweep | demo | api | produce | compactor`
dashboard/                 Vite + React + Recharts; nginx image proxies /api to the api service
tests/                     engine unit tests (every late-data path), simulator properties,
                           100-shipment exit criteria (≤ 10 % ratio, 100 % recall)
reports/                   benchmark-<run>.{md,json,png}, sensitivity.{json,png}
```

Mapping to the PRD's team roles: simulator = `simulator/`; compaction engine
and late-data reconciler = `compaction/engine.py` (+ `tests/test_engine.py`);
storage schema and query API = `db/init.sql`, `storage/`, `api/`; dashboard =
`dashboard/`; benchmark and report = `benchmark/`, `reports/`, this file.

## How the policy works (read this before touching `engine.py`)

Per shipment the engine keeps one small state object. Per reading:

1. **Delay line.** The reading enters a ring buffer holding the last
   `pre_buffer + min_excursion` seconds (10 min + 60 s). Readings *leaving* the
   ring are added to the open summary window. Because the window always lags
   the stream by the buffer, it never overlaps the raw segment a later excursion
   flushes out of the ring — every reading is stored exactly once, which the
   tests assert.
2. **Detector with hysteresis.** `> 8.0 °C` or `< 2.0 °C` starts a *candidate*;
   it is confirmed once it spans 60 s. A reading inside the exit band
   (2.5–7.5 °C) ends an excursion; readings in the gray zone between the
   thresholds neither extend nor end it, so a sensor flapping at 7.9/8.1 °C
   gives one excursion, not fifty. Candidates that never reach 60 s are folded
   into the window (its min/max still shows the blip).
3. **Modes.** Confirming a candidate closes the window, flushes the ring to
   `readings_raw` as the pre-buffer and stores raw until the excursion ends;
   raw continues for the post-buffer, then a new window opens. Windows also
   close on custody handover (configurable) and at a 6 h maximum span.

`detector.find_excursions` is the same rule written over a complete series. It
is the definition used everywhere: the simulator records it as ground truth, the
benchmark measures recall against it, and the engine must reproduce it online
from an arrival-ordered, partly late stream.

### Late and out-of-order data (F8)

A reading older than the shipment's watermark never goes through the state
machine directly:

| late reading lands … | temperature | action | rewrite_log |
|---|---|---|---|
| inside the delay line | any | inserted in order; replay if out of range | — / `split` |
| in a raw segment | in range | stored raw | — |
| in a raw segment | out of range | peak updated; if the bounds move, replay | `split` |
| in a raw segment, inside the excursion | exit band | replay (it may be two excursions) | `split` |
| in a summary window | in range | merged into its stats, version bump | `merge` |
| in a summary window, within staging | out of range | **replay** | `split` |
| in a summary window, older than staging | out of range | stored raw, window min/max widened | `beyond_horizon` |

*Replay* retracts every artifact from the start of the run containing the late
reading (less a pre-buffer) and re-feeds the staged readings through the state
machine. Staging keeps the last 12 h per active shipment (6 h maximum lateness
plus the 6 h maximum window span, so the enclosing window's start is always
available). A burst is settled once it has been quiet for 30 s, or 60 s after
its first reading at the latest — that is the N5 bound. Merges into closed
windows are coalesced per burst, so a 3-hour re-upload yields one `merge` per
touched window and one `split`, not thousands of rows.

The guarantee: an excursion observed at full rate — in order or via replay — is
stored at full rate with its buffers. Only out-of-range readings more than 12 h
late cannot be reconstructed; they are kept raw and logged so the evidence
survives even when its extent is unknown.

### The two comparison stores

The same stream feeds `baseline_hourly` (one mean/min/max per shipment-hour,
buckets held until the shipment ends so late data is folded in as a continuous
aggregate would) and, for the benchmark, `readings_rawstore` (everything).
`?store=baseline|raw` on the trace endpoint runs the same question against either.

### Real stream (optional, F4)

The in-process generator is the default and the benchmark path. The same events
can go through Redpanda (`ingest/kafka.py`): JSON messages keyed by
`shipment_id`, so one partition always carries a whole shipment — exactly the
per-shipment state the engine keeps.

```
docker compose --profile kafka up -d redpanda
coldchain produce --shipments 20            # simulator → topic `readings`
coldchain compactor --truncate              # topic → engine → TimescaleDB
```

Verified: 20 shipments (438,684 readings) through Redpanda produce the identical
store to the in-process run (284 windows, 52 excursions, 15,627 raw rows, 41
rewrites); the consumer sustains ~27 k readings/s including its idle wait.

## Data model

See `db/init.sql`. Hypertables are chunked by 30 days and space-partitioned by
`shipment_id`; `custody` has a GiST index on
`(shipment_id, tstzrange(valid_from, valid_to))` and `holder_at(shipment, ts)`
is the point-in-time join used by the excursion list and detail queries.
Artifact ids are `shipment_id × 10 000 + sequence`, assigned by the engine, so
runs are reproducible and no round trip is needed to write.

## API

`coldchain api` or the compose `api` service; interactive docs at
http://localhost:8000/docs.

| Endpoint | Scenario |
|---|---|
| `GET /api/shipments/{id}/trace?store=excursion_aware\|baseline\|raw` | stitched trace (F11, F13) |
| `GET /api/excursions?holder_type=&min_duration=&…` and `/api/excursions/{id}` | list + custody at start/peak/end (F12, scenario 2) |
| `GET /api/scenarios/hidden` | excursions invisible to hourly means (scenario 1) |
| `GET /api/scenarios/route-risk?group=holder\|type` | legs ranked by excursion-minutes per shipment (scenario 3) |
| `GET /api/storage?shipment_id=` | bytes per policy, recall, per-shipment bytes (scenario 4) |
| `GET /api/rewrites`, `POST /api/demo/late-data` | audit trail; live scenario 5 |

## Results

From `coldchain bench --compress` on the 500-shipment, seed-42 run: 10.56 M
readings, 1,216 injected excursions ≥ 60 s, 2 % excursion rate, 5 % late data,
on a laptop (10-core Apple silicon, 16 GB). Full tables in `reports/`; the
simulator is seeded, so they reproduce exactly.

| Metric | Raw | Uniform hourly | Excursion-aware |
|---|---|---|---|
| Bytes stored (hypertable size, indexes included) | 1.3 GB (100 %) | 4.8 MB (0.37 %) | **77.5 MB (5.99 %)** |
| … with TimescaleDB columnar compression on every policy | 115.9 MB (100 %) | 1.7 MB (1.5 %) | 13.5 MB (11.7 %) |
| Excursion recall (start, end and peak within 10 s) | 100 % | 30.1 % by hourly mean · 100 % by hourly min/max, but hour-level only | **100 %** |
| Peak error (MAE) | 0 °C | 3.55 °C (hourly mean vs true peak) | **0 °C** |
| p95 latency, scenario 1 — hidden excursions | – | – | 16 ms |
| p95 latency, scenario 2 — excursion list with custody holder | – | – | 9 ms |
| p95 latency, scenario 2 — excursion detail, custody at start/peak/end | – | – | 4 ms |
| p95 latency, scenario 3 — route risk ranking | – | – | 10 ms |
| p95 latency, full shipment trace | 32 ms | 1 ms | 3 ms |
| Late-data visibility | n/a | n/a | ≤ 60 s after arrival; 224 replays, 862 logged rewrites, 0 unreconstructable |
| Ingestion, all three policies in one pass incl. COPY to Postgres | 70 k readings/s | same | same |
| Engine alone, in memory (`coldchain simulate`) | – | – | 200–270 k readings/s |

Reading the table:

- **850 of 1,216 excursions (70 %) are invisible to the hourly-mean store**:
  the hour's average stays inside 2–8 °C while the shipment spent 30–60 minutes
  above 10 °C. Hourly min/max would flag the *hour* for all of them but cannot
  say when, for how long, or what the peak was.
- The 10 % target is met on stored bytes. With columnar compression on both
  stores the gap narrows to 8.5×, because a perfectly regular 10-second raw
  series compresses extremely well and our store is already dense. Report both.
- Storage grows with excursion-minutes, not shipment-minutes: the sweep
  (`reports/sensitivity.png`) gives 0.97 % at a 0.5 % excursion rate, 3.9 % at
  2 %, 18.3 % at 10 % — above target when a tenth of all shipment-time is out
  of range, which is the honest limit of the approach. Buffer length moves the
  ratio from 2.5 % (none) to 6.7 % (30 min); the late fraction does not change
  it. Recall is 100 % in every run.
- Latencies are far under the 2 s budget; the scenario queries touch
  `excursions` (1.2 k rows) and the custody index, not the raw tables, so they
  scale with excursion count rather than readings.

### Benchmark method

- **Bytes:** `hypertable_size()` per table after the load (indexes included),
  with and without TimescaleDB columnar compression applied to all policies.
- **Recall:** an injected excursion is recovered when a stored excursion matches
  its start and end within 10 s and its peak exactly. The hourly baseline cannot
  place anything within 10 s, so it is additionally scored on whether any
  overlapping hour's *mean* is out of range (the naive store as the PRD
  describes it) and whether its *min/max* is (the most generous reading, which
  still gives neither timing nor duration).
- **Latency:** 50 runs of each scenario query through the API's own functions;
  p95 reported.
- **Throughput:** readings/s for the whole pipeline, all three policies in one
  pass including COPY into Postgres.

### Threats to validity (say them before the graders do)

Simulated telemetry is smoother than real sensors; thresholds follow WHO 2–8 °C
guidance while real products have product-specific limits; results are from one
machine and say nothing about distributed behaviour; and the custody plan is
known at shipment start rather than streamed.

## Working on it

- Any change to `engine.py`: run `pytest` and `coldchain simulate --shipments
  100 --late-fraction 0.2`. The regression bar is recall 100 %, every reading
  accounted for exactly once, no raw row inside a window range, complete
  ±10 min raw coverage around every ground-truth excursion — the tests check all
  four.
- Any change to `db/init.sql`: `docker compose down -v && docker compose up -d`
  (the schema runs only on an empty volume), then reload.
- The API process must be restarted after editing `api/queries.py` (no
  auto-reload in compose; locally use `coldchain api --reload`).
- Dashboard: `npm run build` must pass with no TypeScript errors before
  rebuilding the image (`docker compose build dashboard`).

## Open questions from the PRD, as decided here

- Custody changes **do** close windows (`close_on_custody_change=True`): it
  makes attribution exact at the cost of ~5 % more window rows.
- Dashboard stack: React + Recharts, not Streamlit.
- Streaming: in-process queue is the default path; Redpanda is an optional
  profile, verified equivalent.
