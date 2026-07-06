-- ============================================================
-- create_loan_eligibility_results_20260531.sql
-- MATERIALIZATION — run this AFTER vw_loan_eligibility_waterfall_v2
-- has been created and verified.
--
-- PURPOSE:
--   Persists the full eligibility result set as a physical Parquet table
--   so that:
--     1. The Python analysis script can query it without loading 20M rows
--        into local memory.
--     2. Repeated ad-hoc queries hit the materialized table, not the live
--        view, eliminating repeated scans of vw2, vw3, account_holder_dump,
--        and fm_momo_loan_portfolio_cummulation.
--     3. Results are stable — re-running the view later (e.g. after a data
--        update) will not silently change previously reported numbers.
--
-- HOW TO USE:
--   1. Run vw_loan_eligibility_waterfall_v2 (CREATE OR REPLACE VIEW) first.
--   2. Run this script once per analysis cycle (~1–2 minutes expected).
--   3. All downstream queries and the Python analysis script should reference
--      wamujap.loan_eligibility_results_20260531 instead of the view.
--
-- TO UPDATE FOR A NEW CYCLE (e.g. June 30 2026):
--   Replace 20260531 → 20260630 throughout this file.
--   Update vw_loan_eligibility_waterfall_v2 params first (analysis_end_date,
--   window_30d_start, window_90d_start, run_date literals, tbl_dt literals).
--   Drop the previous month's results table if no longer needed.
-- ============================================================

-- Safe to re-run — drops previous version of this cycle's table only.
DROP TABLE IF EXISTS wamujap.loan_eligibility_results_20260531;

-- Materialize the full waterfall output as a Parquet table.
-- Column order and names match vw_loan_eligibility_waterfall_v2 exactly.
CREATE TABLE wamujap.loan_eligibility_results_20260531
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['analysis_dt']
)
AS
SELECT
    -- ── Subscriber identity ───────────────────────────────────
    subscriber_msisdn,
    first_activity_dt,
    last_activity_dt,
    total_active_days,

    -- ── Rule 1: registration / activation diagnostics ─────────
    derived_reg_date,
    reg_registration_date,
    reg_activation_date,
    momo_age_days,

    -- ── Eligibility rule flags ────────────────────────────────
    rule1_momo_age_180d_flag,
    rule2_mau30_flag,
    rule3_min_5txn_flag,
    rule4_min_10k_value_flag,
    rule5_no_overdue_60d_flag,
    rule6_no_cooling_off_flag,

    -- ── Waterfall — cumulative eligibility after each rule ────
    eligible_after_rule1,
    eligible_after_rule2,
    eligible_after_rule3,
    eligible_after_rule4,
    eligible_after_rule5,
    proposed_eligible_flag,
    currently_active_borrower_flag,

    -- ── Activity metrics (vw2, 90-day window) ─────────────────
    active_days_last30d,
    momo_txn_cnt_30d,
    momo_inflow_30d,
    momo_outflow_30d,

    -- ── Financial features (vw3) ──────────────────────────────
    outstanding_exposure_amt,
    repayment_ratio_30d,
    repayment_cnt_30d,
    disbursement_cnt_30d,
    wallet_inflow_amt_30d,
    wallet_outflow_amt_30d,
    wallet_txn_cnt_30d,
    wallet_active_days_30d,
    loan_disb_amt_30d,
    loan_repaid_amt_30d,
    days_since_last_disbursement,
    days_since_last_repayment,
    last_disbursement_dt,
    last_repayment_dt,

    -- ── Risk behaviour flags (vw3) ────────────────────────────
    stacked_borrowing_flag_30d,
    repeated_borrowing_flag_30d,
    active_lender_cnt_30d,
    alt_credit_active_flag_30d,

    -- ── Loan portfolio signals (fm_momo_loan_portfolio_cummulation) ──
    overdue_loan_count,
    total_overdue_amount,
    max_days_outstanding,
    cooling_off_loan_count,
    cooling_off_affected_loan_value,
    latest_late_repayment_date,
    latest_cooling_off_end_date,

    -- ── Revenue estimate ──────────────────────────────────────
    estimated_monthly_revenue,

    -- ── Partition column ──────────────────────────────────────
    -- Allows future months to be appended to the same table.
    -- Keep as a DATE literal matching analysis_end_date in the view params.
    DATE '2026-05-31'  AS analysis_dt

FROM vw_loan_eligibility_waterfall_v2;

-- ── Verification ──────────────────────────────────────────────
-- Row count and key flag totals — compare against the view directly
-- to confirm the materialization is complete and consistent.
SELECT
    COUNT(*)                         AS total_rows,
    SUM(proposed_eligible_flag)      AS eligible_total,
    SUM(eligible_after_rule1)        AS after_rule1,
    SUM(eligible_after_rule2)        AS after_rule2,
    SUM(eligible_after_rule3)        AS after_rule3,
    SUM(eligible_after_rule4)        AS after_rule4,
    SUM(eligible_after_rule5)        AS after_rule5,
    SUM(currently_active_borrower_flag) AS active_borrowers,
    ROUND(SUM(estimated_monthly_revenue), 0) AS est_monthly_revenue
FROM wamujap.loan_eligibility_results_20260531
WHERE analysis_dt = DATE '2026-05-31';
