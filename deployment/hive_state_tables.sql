-- Hive DDL for cross-cycle state persistence
-- Run once per environment via PrestoDB before first deployment.
-- Requires write access to the hive catalog and the credit_engine schema.
--
-- Re-running is safe: all statements use IF NOT EXISTS.
-- Partitioned by run_date (VARCHAR 'YYYY-MM-DD') so each daily run writes
-- exactly one partition; load functions always SELECT MAX(run_date).

-- Step 1: Schema
CREATE SCHEMA IF NOT EXISTS hive.credit_engine;

-- Step 2: Credit limits from the previous daily run
--         Used by the decision engine for stability smoothing.
CREATE TABLE IF NOT EXISTS hive.credit_engine.pipeline_capacity_state (
    subscriber_msisdn  VARCHAR,
    credit_limit       DOUBLE,
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
    run_date                    VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);
