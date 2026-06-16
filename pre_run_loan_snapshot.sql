-- ============================================================
-- pre_run_loan_snapshot.sql
-- PRE-MATERIALIZATION STEP — run this BEFORE querying the waterfall.
--
-- PURPOSE:
--   fm_momo_loan_portfolio_cummulation is not partitioned by tbl_dt.
--   A COUNT(*) WHERE tbl_dt = 20260531 takes ~1m 45s (full table scan).
--   Running the waterfall directly against the source takes 50+ minutes.
--
--   This script creates a small snapshot table for the relevant date.
--   The waterfall then reads from loan_snapshot_20260531 (fast, small)
--   instead of the full historical table (slow, unpartitioned).
--
-- HOW TO USE:
--   1. Run this script once per analysis cycle (takes ~2 minutes).
--   2. Query vw_loan_eligibility_waterfall_v2 normally — it will
--      reference loan_snapshot_20260531 instead of the source table.
--
-- TO UPDATE FOR A NEW SNAPSHOT DATE (e.g. June 30 2026):
--   Change 20260531 → 20260630 and 20240101 → 20240101 (keep lookback).
--   Drop the old snapshot table if no longer needed.
-- ============================================================

-- Drop previous snapshot if it exists (safe to re-run)
DROP TABLE IF EXISTS wamujap.loan_snapshot_20260531;

-- Create the snapshot: one tbl_dt, loans from Jan 2024 onwards only.
-- disbursement_date >= 20240101 is a raw integer filter applied at scan time
-- before any date conversion — eliminates pre-2024 rows cheaply.
-- Loans before Jan 2024 cannot affect Rule 5 (overdue > 60d) or
-- Rule 6 (cooling-off 90d after repayment) for a May 2026 analysis date.
CREATE TABLE wamujap.loan_snapshot_20260531 AS
SELECT *
FROM wamujap.fm_momo_loan_portfolio_cummulation
WHERE tbl_dt            = 20260531
  AND disbursement_date IS NOT NULL
  AND disbursement_date >= 20240101;

-- Verify row count (should complete in seconds against the new table)
SELECT COUNT(*) AS snapshot_rows FROM wamujap.loan_snapshot_20260531;
