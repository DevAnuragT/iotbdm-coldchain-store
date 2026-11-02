# PRD: Excursion-Aware Cold-Chain Data Store

2 Nov 2026 · @Anurag

## Overview

We will build a software-only data platform that stores years of simulated vaccine cold-chain sensor data at under 10% of raw size while guaranteeing that no temperature excursion is ever lost. The novelty is an excursion-aware compaction policy: steady periods collapse into summaries, while every reading around an out-of-range event is kept at full resolution with a before/after buffer. A temporal custody join answers who held a shipment at the moment of any excursion.

The project is for the Internet of Things Big Data Management (IoTBDM) course. No hardware is used; a simulator replays realistic shipment telemetry. The deliverable is a working pipeline, a storage engine, a query layer, a dashboard, and a benchmark against naive uniform downsampling.

## Problem statement

Vaccines must stay between 2°C and 8°C from factory to clinic; a single shipment logs a reading every 10 seconds, so a national programme produces billions of readings a year. Today's systems handle this badly in one of two ways:

- **Store everything raw.** Simple and lossless, but storage and query cost grow linearly forever. Most readings say "4°C, still fine" and carry no information.
- **Downsample uniformly.** Keep one average per hour. Cheap, but a 40-minute spike to 11°C averaged into an hour reads as 6°C. The evidence of a spoiled batch is destroyed.

Commercial cold-chain products (Controlant, Sensitech, Berlinger) focus on real-time alerting and leave long-term storage as a dumb bucket. No mainstream time-series database (InfluxDB, TimescaleDB, Prometheus) offers compaction driven by domain events. The gap this project fills: a storage lifecycle that knows what an excursion is, keeps it in full, and still shrinks the rest.

The question a regulator or insurer asks months later, "Was this batch ever unsafe, for how long, and who had it?", must be answerable from compacted data with the same certainty as from raw.

## Goals and non-goals

### Goals

1. Reduce stored volume to ≤ 10% of raw while preserving 100% of excursions longer than 1 minute.
2. Answer excursion and custody queries over 12 months of simulated data in under 2 seconds.
3. Demonstrate, with a side-by-side benchmark, that uniform hourly downsampling misses excursions our method keeps.
4. Handle out-of-order and late-arriving readings without corrupting already-closed summaries.
5. Deliver a live demo runnable on one laptop in under 10 minutes.

### Non-goals

- Building or integrating physical sensors or gateways.
- Real-time SMS/email alerting (exists commercially; not a BDM contribution).
- Machine-learning prediction of future excursions.
- Production-grade security, multi-tenancy or cloud deployment.

## Target users

| Persona | Role | What they need from the system |
|---|---|---|
| Cold-chain compliance officer | Audits shipments for a vaccine distributor | Proof that a batch stayed in range, or exact timing and custody of any excursion |
| Logistics operations analyst | Monitors fleet and warehouse performance | Which routes, depots or carriers cause the most excursions, over months |
| Insurance or regulatory investigator | Resolves spoilage claims after the fact | Full-resolution evidence around an incident, months after it happened |
| Data platform engineer | Runs the storage system | Predictable storage growth and query latency as shipment count scales |

## Key use cases and demo scenarios

1. **Hidden excursion.** A shipment's hourly average stays within 2–8°C, but it spent 35 minutes at 10.5°C. Query: list all shipments whose hourly aggregates look safe but contain an excursion. Naive storage returns none; ours returns the shipment with a full-resolution trace.
2. **Custody attribution.** For a given excursion, show which depot, truck or clinic held the shipment at its start, peak and end, with handover timestamps.
3. **Route risk ranking.** Across 12 months, rank transport legs by total excursion-minutes per shipment.
4. **Storage audit.** Show bytes stored per shipment under raw, uniform-hourly and excursion-aware policies, with excursion recall for each.
5. **Late data.** A truck re-connects after 3 hours and uploads readings that include an excursion inside a summary already written. The system reopens, splits and re-compacts that window; the excursion appears in the next query.

The live demo walks scenarios 1, 2 and 4 in that order; 3 and 5 are shown if time allows.

## Functional requirements

| ID | Component | Requirement |
|---|---|---|
| F1 | Simulator | Generate ≥ 5,000 shipments over a 12-month virtual period, each with temperature, humidity and GPS at 10 s intervals, realistic diurnal drift, sensor noise and configurable excursion injection (rate, duration, magnitude). |
| F2 | Simulator | Emit a custody event log per shipment: handovers between factory, depot, truck, airport and clinic with timestamps. |
| F3 | Simulator | Support late and out-of-order delivery of a configurable fraction of readings (default 5%, delay up to 6 h). |
| F4 | Ingestion | Accept readings as a stream (Kafka topic or in-process queue) at ≥ 20,000 readings/s on one laptop; validate schema and units. |
| F5 | Compaction | Detect excursions online: temperature outside 2–8°C for ≥ 60 s, with hysteresis to avoid flapping at the threshold. |
| F6 | Compaction | Keep full-resolution readings from 10 min before an excursion starts to 10 min after it ends (both configurable). |
| F7 | Compaction | Collapse in-range windows into summaries: window start/end, count, min, max, mean, standard deviation, and last reading. |
| F8 | Compaction | Reopen, split and re-compact a closed window when late data lands inside it; log every rewrite. |
| F9 | Storage | Store summaries and raw excursion segments in a time-series database with per-shipment partitioning and time-range indexing. |
| F10 | Storage | Store custody intervals as [valid_from, valid_to) ranges per shipment with a range index. |
| F11 | Query | Return the full trace of any shipment over any time range, stitching summaries and raw segments transparently. |
| F12 | Query | List excursions filtered by shipment, carrier, depot, duration or magnitude; return start, end, peak and custody holder. |
| F13 | Query | Expose a comparison mode that runs the same query against a uniform-hourly baseline store. |
| F14 | Dashboard | Web UI with shipment trace chart, excursion list, custody timeline and storage-comparison panel. |
| F15 | Benchmark | Script that reports bytes stored, excursion recall and p95 query latency for raw, uniform-hourly and excursion-aware stores. |

## Non-functional requirements

| ID | Attribute | Target |
|---|---|---|
| N1 | Ingestion throughput | ≥ 20,000 readings/s sustained on a laptop with 16 GB RAM |
| N2 | Storage ratio | Excursion-aware store ≤ 10% of raw bytes at a 2% excursion rate |
| N3 | Excursion recall | 100% of injected excursions ≥ 60 s are recoverable at full resolution |
| N4 | Query latency | p95 < 2 s for excursion list and custody join over 12 months, 5,000 shipments |
| N5 | Late-data correctness | A reading arriving up to 6 h late is reflected in queries within 60 s of arrival |
| N6 | Reproducibility | Simulator is seeded; the same seed yields identical data and benchmark numbers |
| N7 | Portability | Whole stack starts with one `docker compose up` command |

## System architecture and data model

Readings flow top-down; the same stream also feeds a dashed baseline path (uniform hourly downsampling) so every query can be run against both stores in the demo.

### Data model

| Table | Key columns | Notes |
|---|---|---|
| readings_raw (excursion segments) | shipment_id, ts, temp_c, humidity_pct, lat, lon, excursion_id | Only readings inside an excursion window plus its ±10 min buffer |
| summary_windows | shipment_id, window_start, window_end, n, min_c, max_c, mean_c, sd_c, last_c, version | One row per contiguous in-range run; version increments on every late-data rewrite |
| excursions | excursion_id, shipment_id, start_ts, end_ts, peak_c, duration_s, detected_at | Index on (shipment_id, start_ts) and on duration_s |
| custody | shipment_id, holder_id, holder_type, valid_from, valid_to | Half-open intervals; GiST or BRIN range index for point-in-time joins |
| holders | holder_id, name, type (factory, depot, truck, airport, clinic), region | Dimension table |
| rewrite_log | window_id, old_version, new_version, reason, late_reading_ts, rewritten_at | Audit trail for late-data reconciliation |
| baseline_hourly | shipment_id, hour, mean_c, min_c, max_c | Naive comparison store; never consulted by the main query path |

## Novelty: excursion-aware compaction

The contribution is a storage policy where domain semantics, not a fixed clock, decide what is kept. Existing time-series databases downsample by elapsed time; ours downsamples by information content, defined for cold-chain as proximity to a safety excursion.

### How the policy works

1. Every incoming reading enters a per-shipment ring buffer holding the last 10 minutes at full rate.
2. A detector with hysteresis (enter at > 8.0°C or < 2.0°C, exit at < 7.5°C and > 2.5°C, minimum 60 s) decides whether the shipment is in an excursion.
3. In range: readings accumulate into an open summary window (count, min, max, mean, sd). The window closes when the shipment leaves range, changes custody, or reaches a maximum span (default 6 h).
4. Entering an excursion: the open window closes, the ring buffer is flushed to readings_raw as the pre-buffer, and all readings are stored raw until exit.
5. After exit: raw storage continues for 10 more minutes (post-buffer), then a new summary window opens.
6. A late reading that falls inside a closed window reopens it, replays the detector over the window's reconstructed range using the stored min/max as a conservative check, splits the window if an excursion is now present, and writes a rewrite_log entry.

### The guarantee

Any excursion that the detector observes at full rate is stored at full rate with its buffers, so excursion recall is 100% by construction for events ≥ 60 s. The cost is bounded: storage grows with excursion-minutes, not shipment-minutes. Uniform downsampling has no such guarantee, which the benchmark makes visible.

### Why this is a BDM contribution, not an alerting feature

Alerting answers "is something wrong now?" from the live stream. This policy answers "what was true then?" from compacted history, with provable fidelity and an audit trail of every rewrite. That is a data-lifecycle problem: retention, compaction, late-arrival handling and query-time reconstruction, each shaped by the domain.

## Evaluation plan and benchmarks

All runs use the seeded simulator at 5,000 shipments over 12 virtual months with a 2% excursion rate, so numbers are reproducible. Three storage policies are compared on the same input.

| Metric | How measured | Raw | Uniform hourly | Excursion-aware (target) |
|---|---|---|---|---|
| Bytes stored | Table sizes after final compaction | 100% | ~0.3% | ≤ 10% |
| Excursion recall | Injected excursions ≥ 60 s recoverable with start, end and peak within 10 s | 100% | measured (expected < 40%) | 100% |
| Peak error | Mean absolute error of recovered peak temperature | 0°C | measured | 0°C |
| p95 query latency | 50 runs each of scenarios 1–3 | measured | measured | < 2 s |
| Late-data latency | Time from late reading arrival to visibility in queries | n/a | n/a | < 60 s |
| Ingestion throughput | Readings/s sustained for 10 min | measured | measured | ≥ 20,000 |

Sensitivity runs: excursion rate 0.5%, 2%, 10%; buffer length 0, 5, 10, 30 min; late-data fraction 0%, 5%, 20%. Each run plots storage ratio against recall to show the trade-off curve.

Threats to validity to state in the report: simulated data may be smoother than real sensors; excursion definitions follow WHO 2–8°C guidance but real products have product-specific limits; single-machine results will not reflect distributed behaviour.

## Tech stack and team roles

| Layer | Choice | Why |
|---|---|---|
| Simulator | Python 3.11, NumPy, Faker | Fast to write; seeded randomness for reproducibility |
| Streaming | Apache Kafka via Redpanda Docker image (fallback: Python asyncio.Queue) | Real stream semantics for the report; in-process fallback keeps the demo safe |
| Compaction engine | Python service consuming the stream | Where the novelty lives; pure Python keeps it readable for graders |
| Storage | PostgreSQL 16 + TimescaleDB (hypertables, native compression) | Range types and GiST indexes for custody joins; compression stats for the benchmark |
| Query API | FastAPI | Thin layer exposing scenario queries and comparison mode |
| Dashboard | React + Recharts, or Streamlit if time is short | Trace chart, excursion list, custody timeline, storage panel |
| Orchestration | Docker Compose | One-command start on any laptop |
| Benchmark | Python script + pytest | Produces the tables and plots used in the report |

### Team roles (adjust to team size)

- Simulator and data generation: 1 person
- Compaction engine and late-data reconciler: 1–2 people (core of the project)
- Storage schema, indexing and query API: 1 person
- Dashboard and demo: 1 person
- Benchmark, report and slides: shared, led by 1 person

## Milestones and timeline

The compaction engine gets three weeks because it is the graded novelty; the dashboard starts only once the storage schema is stable. Shift every date if your submission deadline differs from 21 Dec.

### Exit criteria per milestone

- **Setup and simulator:** `docker compose up` works; simulator emits 1 shipment-day with injected excursions and custody events.
- **Ingestion and storage:** readings land in TimescaleDB; raw-policy trace query works.
- **Compaction engine:** storage ratio ≤ 10% and 100% recall on a 100-shipment test set.
- **Late-data reconciler:** scenario 5 passes; rewrite log populated.
- **Query API and dashboard:** scenarios 1, 2 and 4 run end to end from the UI.
- **Benchmark and report:** all metrics in the evaluation table filled; report draft complete.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Kafka setup eats days of the schedule | Medium | Medium | Build on an in-process queue first; swap in Redpanda only once the engine works |
| Late-data reconciler proves harder than planned | High | High | Ship v1 with a simplified rule (any late reading inside a closed window forces full re-read of that window from a raw staging table kept for 6 h); refine if time allows |
| Detector flaps at the 8°C boundary and inflates storage | Medium | Medium | Hysteresis band and 60 s minimum; tune on the sensitivity runs |
| Graders say "this already exists" | Medium | High | Lead with the storage-vs-recall benchmark and the rewrite audit trail; cite commercial systems as motivation, not competition |
| Live demo fails on the day | Low | High | Pre-loaded database snapshot plus recorded video fallback; rehearsal milestone on 19 Dec |
| Team member drops or is unavailable | Medium | Medium | Compaction engine owned by two people; every component has a README |

## Open questions and future work

### To decide before week 2

- [ ] Confirm the submission date and demo format (live, recorded, or both) with the course instructor.
- [ ] Team size and final role assignment.
- [ ] Dashboard stack: React (richer, slower to build) or Streamlit (faster, plainer).
- [ ] Should custody changes also force a window close, or only excursions? Forcing close helps attribution but increases row count.

### Future work to mention in the report

- Adaptive buffer length based on how fast temperature was rising before the excursion.
- Distributing the compaction engine across partitions with Kafka consumer groups.
- Generalising the policy to other domains with safety thresholds: pharmaceutical warehouses, blood banks, food logistics, lithium battery shipping.
- Formal proof of the recall guarantee under bounded late-arrival delay.
