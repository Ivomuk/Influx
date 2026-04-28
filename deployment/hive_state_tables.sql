-- Hive DDL for cross-cycle state persistence
-- Run once per environment via PrestoDB before first deployment.
-- Requires write access to the hive catalog and the credit_engine schema.
--
-- Re-running is safe: all statements use IF NOT EXISTS.
-- Tables are partitioned by run_date (VARCHAR 'YYYY-MM-DD').
--
-- pipeline_commit_log is the authoritative commit marker. load_cross_cycle_state
-- reads only run_dates that appear there as COMMITTED, so a partial write
-- (where data inserts succeed but the commit log write fails) is invisible
-- to the next pipeline run.
--
-- Idempotency: pipeline_run_id is recorded in every row. Before writing,
-- persist_cross_cycle_state checks the commit log for an existing COMMITTED
-- entry with the same pipeline_run_id and skips if found.

-- Step 1: Schema
CREATE SCHEMA IF NOT EXISTS hive.credit_engine;

-- Step 2: Credit limits from the previous daily run
--         Used by the decision engine for stability smoothing.
CREATE TABLE IF NOT EXISTS hive.credit_engine.pipeline_capacity_state (
    subscriber_msisdn  VARCHAR,
    credit_limit       DOUBLE,
    pipeline_run_id    VARCHAR,
    run_date           VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 3: Subscriber operating states from the previous daily run
--         Used by Layer 5 for persistence enforcement.
CREATE TABLE IF NOT EXISTS hive.credit_engine.pipeline_subscriber_state (
    subscriber_msisdn  VARCHAR,
    operating_state    VARCHAR,
    state_change_dt    TIMESTAMP,
    pipeline_run_id    VARCHAR,
    run_date           VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 4: Pipeline-level scalars per daily run
--         certification_summary is JSON-serialised (excludes per-check detail rows).
CREATE TABLE IF NOT EXISTS hive.credit_engine.pipeline_run_metadata (
    circuit_breaker_multiplier  DOUBLE,
    operating_mode              VARCHAR,
    certification_passed        BOOLEAN,
    certification_summary       VARCHAR,
    pipeline_run_id             VARCHAR,
    run_date                    VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 5: Commit marker — written LAST after all data tables succeed.
--         load_cross_cycle_state resolves the target run_date from this table
--         so partial writes are never loaded.
CREATE TABLE IF NOT EXISTS hive.credit_engine.pipeline_commit_log (
    pipeline_run_id  VARCHAR,
    write_status     VARCHAR,   -- 'COMMITTED'
    committed_at     VARCHAR,   -- ISO-8601 UTC timestamp
    run_date         VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);
