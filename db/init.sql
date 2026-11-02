-- Excursion-aware cold-chain data store: schema (F9, F10).
-- Runs once when the TimescaleDB container initialises an empty volume.

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- int + range in one GiST index

-- ---------------------------------------------------------------- dimensions
CREATE TABLE holders (
    holder_id   int PRIMARY KEY,
    name        text NOT NULL,
    type        text NOT NULL CHECK (type IN ('factory','depot','truck','airport','clinic')),
    region      text NOT NULL
);

CREATE TABLE shipments (
    shipment_id     int PRIMARY KEY,
    origin_id       int NOT NULL REFERENCES holders,
    destination_id  int NOT NULL REFERENCES holders,
    start_ts        timestamptz NOT NULL,
    end_ts          timestamptz NOT NULL,
    product         text NOT NULL
);
CREATE INDEX shipments_start_idx ON shipments (start_ts);

-- Custody as half-open intervals; the GiST index serves point-in-time joins
-- ("who held shipment S at instant T").
CREATE TABLE custody (
    shipment_id  int NOT NULL REFERENCES shipments,
    holder_id    int NOT NULL REFERENCES holders,
    holder_type  text NOT NULL,
    valid_from   timestamptz NOT NULL,
    valid_to     timestamptz NOT NULL,
    PRIMARY KEY (shipment_id, valid_from),
    CHECK (valid_from < valid_to)
);
CREATE INDEX custody_range_idx ON custody USING gist (shipment_id, tstzrange(valid_from, valid_to, '[)'));
CREATE INDEX custody_holder_idx ON custody (holder_id, valid_from);

-- ------------------------------------------------------ excursion-aware store
-- Full-resolution readings, only inside an excursion window and its buffers.
CREATE TABLE readings_raw (
    shipment_id   int NOT NULL,
    ts            timestamptz NOT NULL,
    temp_c        real NOT NULL,
    humidity_pct  real NOT NULL,
    lat           real NOT NULL,
    lon           real NOT NULL,
    excursion_id  bigint
);
SELECT create_hypertable('readings_raw', 'ts', chunk_time_interval => INTERVAL '30 days');
SELECT add_dimension('readings_raw', 'shipment_id', number_partitions => 4);
CREATE INDEX readings_raw_ship_ts_idx ON readings_raw (shipment_id, ts);
CREATE INDEX readings_raw_exc_idx ON readings_raw (excursion_id, ts);

-- One row per contiguous in-range run. Version increments on every rewrite.
CREATE TABLE summary_windows (
    window_id     bigint NOT NULL,
    shipment_id   int NOT NULL,
    window_start  timestamptz NOT NULL,
    window_end    timestamptz NOT NULL,
    n             int NOT NULL,
    min_c         real NOT NULL,
    max_c         real NOT NULL,
    mean_c        real NOT NULL,
    sd_c          real NOT NULL,
    last_c        real NOT NULL,
    version       int NOT NULL DEFAULT 1
);
SELECT create_hypertable('summary_windows', 'window_start', chunk_time_interval => INTERVAL '30 days');
CREATE UNIQUE INDEX summary_windows_pk ON summary_windows (shipment_id, window_start, window_id);
CREATE INDEX summary_windows_id_idx ON summary_windows (window_id);

CREATE TABLE excursions (
    excursion_id  bigint PRIMARY KEY,
    shipment_id   int NOT NULL,
    start_ts      timestamptz NOT NULL,
    end_ts        timestamptz NOT NULL,
    peak_c        real NOT NULL,
    duration_s    int NOT NULL,
    detected_at   timestamptz NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('hot','cold'))
);
CREATE INDEX excursions_ship_start_idx ON excursions (shipment_id, start_ts);
CREATE INDEX excursions_duration_idx ON excursions (duration_s);
CREATE INDEX excursions_start_idx ON excursions (start_ts);

-- Audit trail for late-data reconciliation (F8).
CREATE TABLE rewrite_log (
    id               bigserial PRIMARY KEY,
    window_id        bigint,
    shipment_id      int NOT NULL,
    old_version      int NOT NULL,
    new_version      int NOT NULL,
    reason           text NOT NULL,
    late_reading_ts  timestamptz NOT NULL,
    rewritten_at     timestamptz NOT NULL,
    detail           text
);
CREATE INDEX rewrite_log_ship_idx ON rewrite_log (shipment_id, rewritten_at);

-- ------------------------------------------------------------ comparison stores
-- Naive policy: one row per shipment-hour. Never consulted by the main path.
CREATE TABLE baseline_hourly (
    shipment_id  int NOT NULL,
    hour         timestamptz NOT NULL,
    n            int NOT NULL,
    mean_c       real NOT NULL,
    min_c        real NOT NULL,
    max_c        real NOT NULL
);
SELECT create_hypertable('baseline_hourly', 'hour', chunk_time_interval => INTERVAL '30 days');
CREATE UNIQUE INDEX baseline_hourly_pk ON baseline_hourly (shipment_id, hour);

-- "Store everything" policy, for the benchmark only. Same column types as
-- readings_raw so the byte comparison is like for like.
CREATE TABLE readings_rawstore (
    shipment_id   int NOT NULL,
    ts            timestamptz NOT NULL,
    temp_c        real NOT NULL,
    humidity_pct  real NOT NULL,
    lat           real NOT NULL,
    lon           real NOT NULL
);
SELECT create_hypertable('readings_rawstore', 'ts', chunk_time_interval => INTERVAL '30 days');
SELECT add_dimension('readings_rawstore', 'shipment_id', number_partitions => 4);
CREATE INDEX readings_rawstore_ship_ts_idx ON readings_rawstore (shipment_id, ts);

-- ---------------------------------------------------------------- benchmark
-- What the simulator injected, so recall can be computed in SQL.
CREATE TABLE ground_truth (
    shipment_id  int NOT NULL,
    start_ts     timestamptz NOT NULL,
    end_ts       timestamptz NOT NULL,
    peak_c       real NOT NULL,
    duration_s   int NOT NULL,
    PRIMARY KEY (shipment_id, start_ts)
);

CREATE TABLE runs (
    run_id       text PRIMARY KEY,
    seed         int NOT NULL,
    n_shipments  int NOT NULL,
    config       jsonb NOT NULL,
    started_at   timestamptz NOT NULL,
    finished_at  timestamptz,
    readings     bigint,
    seconds      double precision,
    stats        jsonb
);

CREATE TABLE benchmark_results (
    run_id       text NOT NULL,
    policy       text NOT NULL,
    metric       text NOT NULL,
    value        double precision,
    unit         text,
    detail       jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, policy, metric)
);

-- ------------------------------------------------------------------- views
-- Custody holder at an instant, used by the excursion list and detail queries.
CREATE OR REPLACE FUNCTION holder_at(p_shipment int, p_ts timestamptz)
RETURNS TABLE (holder_id int, holder_type text, name text, region text, valid_from timestamptz, valid_to timestamptz)
LANGUAGE sql STABLE AS $$
    SELECT c.holder_id, c.holder_type, h.name, h.region, c.valid_from, c.valid_to
    FROM custody c JOIN holders h USING (holder_id)
    WHERE c.shipment_id = p_shipment
      AND tstzrange(c.valid_from, c.valid_to, '[)') @> p_ts
    LIMIT 1
$$;
