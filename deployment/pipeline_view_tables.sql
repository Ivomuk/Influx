-- Hive DDL for the six pipeline view tables (vw1–vw6).
-- Run once per environment via PrestoDB before first deployment.
-- Requires write access to the hive catalog and the credit_engine schema.
--
-- Re-running is safe: all statements use IF NOT EXISTS.
-- Tables are partitioned by run_date (VARCHAR 'YYYY-MM-DD').
--
-- These tables replace Presto lazy views for environments where CREATE VIEW
-- is not permitted. The batch engine (PrestoSQLBatchEngine) extracts the
-- SELECT body from each .sql file and materialises it here via CTAS on the
-- first run and INSERT OVERWRITE on subsequent runs.
--
-- Dependency order for daily materialisation:
--   vw1 → vw2 → vw3 → vw4 → vw5 → vw6
-- vw3–vw6 reference upstream tables by their unqualified names, which Presto
-- resolves to hive.credit_engine.* via the session's default catalog/schema.

-- Step 1: Schema
CREATE SCHEMA IF NOT EXISTS hive.credit_engine;

-- Step 2: Raw normalised events
--         Source: analytics.momo_tran_loc_mapping_v2
--         Granularity: one row per raw transaction event
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw1_credit_v1_normalized_events (
    event_dt                  DATE,
    inserted_dt               TIMESTAMP,
    fid                       VARCHAR,
    service_name              VARCHAR,
    sub_service_name          VARCHAR,
    instruct_hdr_type         VARCHAR,
    from_msisdn               VARCHAR,
    to_msisdn                 VARCHAR,
    txn_value_num             DOUBLE,
    from_profile              VARCHAR,
    to_profile                VARCHAR,
    from_sp                   VARCHAR,
    to_sp                     VARCHAR,
    event_family              VARCHAR,
    subscriber_msisdn         VARCHAR,
    loan_counterparty_msisdn  VARCHAR,
    lender_family_v1          VARCHAR,
    disbursement_amt          DOUBLE,
    repayment_amt             DOUBLE,
    wallet_inflow_amt         DOUBLE,
    wallet_outflow_amt        DOUBLE,
    spend_amt                 DOUBLE,
    alt_credit_signal_flag    INTEGER,
    savings_amt               DOUBLE,
    signed_amount             DOUBLE,
    run_date                  VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 3: Daily rollup per subscriber
--         Source: vw1_credit_v1_normalized_events
--         Granularity: one row per (event_dt, subscriber_msisdn)
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw2_credit_v1_subscriber_day (
    event_dt                   DATE,
    subscriber_msisdn          VARCHAR,
    loan_disb_amt_day          DOUBLE,
    loan_repaid_amt_day        DOUBLE,
    wallet_inflow_amt_day      DOUBLE,
    wallet_outflow_amt_day     DOUBLE,
    spend_amt_day              DOUBLE,
    savings_amt_day            DOUBLE,
    loan_disb_cnt_day          BIGINT,
    loan_repay_cnt_day         BIGINT,
    wallet_txn_cnt_day         BIGINT,
    lender_family_cnt_day      BIGINT,
    had_disbursement_day       INTEGER,
    had_repayment_day          INTEGER,
    alt_credit_signal_flag_day INTEGER,
    run_date                   VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 4: Layer 1 features — rolling windows (7/14/30/60/90-day),
--         anti-gaming flags, identity maturity signals.
--         Source: vw2_credit_v1_subscriber_day
--         Granularity: one row per (feature_dt, subscriber_msisdn)
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw3_credit_v1_layer1_features (
    feature_dt                             DATE,
    subscriber_msisdn                      VARCHAR,
    outstanding_exposure_amt               DOUBLE,
    loan_disb_amt_30d                      DOUBLE,
    loan_repaid_amt_30d                    DOUBLE,
    last_disbursement_dt                   DATE,
    last_repayment_dt                      DATE,
    repayment_ratio_30d                    DOUBLE,
    repayment_ratio_14d                    DOUBLE,
    repayment_ratio_60d                    DOUBLE,
    repayment_ratio_90d                    DOUBLE,
    repayment_ratio_trend_7d               DOUBLE,
    days_since_last_disbursement           BIGINT,
    wallet_inflow_amt_30d                  DOUBLE,
    wallet_outflow_amt_30d                 DOUBLE,
    spend_amt_30d                          DOUBLE,
    savings_amt_30d                        DOUBLE,
    wallet_txn_cnt_30d                     BIGINT,
    wallet_active_days_30d                 BIGINT,
    wallet_inflow_amt_7d                   DOUBLE,
    wallet_inflow_amt_14d                  DOUBLE,
    wallet_inflow_amt_60d                  DOUBLE,
    wallet_inflow_amt_90d                  DOUBLE,
    wallet_active_days_7d                  BIGINT,
    wallet_active_days_14d                 BIGINT,
    wallet_active_days_60d                 BIGINT,
    wallet_active_days_90d                 BIGINT,
    wallet_inflow_trend_7d_vs_90d          DOUBLE,
    wallet_activity_last_7d               DOUBLE,
    wallet_activity_prev_7d               DOUBLE,
    post_loan_wallet_activity_change_ratio DOUBLE,
    disbursement_cnt_30d                   BIGINT,
    disbursement_cnt_7d                    BIGINT,
    disbursement_cnt_14d                   BIGINT,
    disbursement_cnt_60d                   BIGINT,
    disbursement_cnt_90d                   BIGINT,
    repayment_cnt_30d                      BIGINT,
    repayment_cnt_7d                       BIGINT,
    days_since_last_repayment              BIGINT,
    active_lender_cnt_30d                  BIGINT,
    active_lender_days_30d                 BIGINT,
    alt_credit_active_flag_30d             INTEGER,
    stacked_borrowing_flag_30d             INTEGER,
    repeated_borrowing_flag_30d            INTEGER,
    timing_manipulation_flag               INTEGER,
    suspicious_repayment_jump_flag         INTEGER,
    loan_cycling_flag                      INTEGER,
    sim_age_days                           BIGINT,
    has_inflow_and_spend_flag              INTEGER,
    sim_age_cohort                         VARCHAR,
    identity_confidence_score_v2           DOUBLE,
    run_date                               VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 5: Layer 0 scores — 10 deterministic risk scores derived from vw3 features.
--         Source: vw3_credit_v1_layer1_features
--         Granularity: one row per (feature_dt, subscriber_msisdn)
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw4_credit_v1_layer0_scores (
    feature_dt                                       DATE,
    subscriber_msisdn                                VARCHAR,
    expected_repayment_probability_v1_rule           DOUBLE,
    expected_credit_loss_rate_v1_rule                DOUBLE,
    churn_cooling_probability_v1_rule                DOUBLE,
    expected_future_transaction_margin_score_v1_rule DOUBLE,
    expected_treatment_cost_score_v1_rule            DOUBLE,
    customer_lifetime_value_contribution_v1_rule     DOUBLE,
    debt_stress_index_v1                             BIGINT,
    fraud_abuse_risk_score_v1_rule                   DOUBLE,
    behavior_consistency_score_v1_rule               DOUBLE,
    identity_confidence_score_v1_rule                DOUBLE,
    prob_distressed_v1_rule                          DOUBLE,
    prob_at_risk_v1_rule                             DOUBLE,
    prob_cooling_off_v1_rule                         DOUBLE,
    prob_healthy_v1_rule                             DOUBLE,
    timing_manipulation_flag                         INTEGER,
    suspicious_repayment_jump_flag                   INTEGER,
    loan_cycling_flag                                INTEGER,
    sim_age_days                                     BIGINT,
    sim_age_cohort                                   VARCHAR,
    has_inflow_and_spend_flag                        INTEGER,
    repayment_ratio_trend_7d                         DOUBLE,
    wallet_inflow_trend_7d_vs_90d                    DOUBLE,
    run_date                                         VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 6: Policy-authoritative reason codes — one categorical code per subscriber.
--         Source: vw4_credit_v1_layer0_scores
--         Granularity: one row per (feature_dt, subscriber_msisdn)
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw5_credit_v1_reason_codes (
    feature_dt          DATE,
    subscriber_msisdn   VARCHAR,
    primary_reason_code VARCHAR,
    run_date            VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);

-- Step 7: Conservative credit cap and recommended action per subscriber.
--         Source: vw4_credit_v1_layer0_scores + vw5_credit_v1_reason_codes
--         Granularity: one row per (feature_dt, subscriber_msisdn)
CREATE TABLE IF NOT EXISTS hive.credit_engine.vw6_credit_v1_cap_and_action (
    feature_dt                   DATE,
    subscriber_msisdn            VARCHAR,
    primary_reason_code          VARCHAR,
    recommended_action           VARCHAR,
    conservative_credit_limit_v1 DOUBLE,
    run_date                     VARCHAR
)
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['run_date']
);
