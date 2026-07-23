-- ============================================================
-- create_loan_eligibility_waterfall_v3_20260531.sql
-- MATERIALIZATION — run this to persist v3 results as a physical table.
--
-- PURPOSE:
--   Persists the full eligibility result set as a physical Parquet
--   table so that:
--     1. Downstream queries hit a fast materialized table rather than
--        re-scanning vw2, vw3, account_holder_dump, and the loan tracker.
--     2. Results are stable — re-running after a data refresh will not
--        silently change reported numbers.
--     3. The 20M-row dataset can be queried without a local OOM.
--
-- KEY DIFFERENCE vs v2 CTAS:
--   Loan data source replaced: wamujap.fm_momo_loan_portfolio_cummulation
--   → analytics.momo_loan_book_tracker_loan_state_daily.
--   - Rule 5: uses days_aging > 60 (pre-computed) instead of date arithmetic.
--   - Rule 6: uses loan_status IN ('CLOSED','SETTLED') and native DATE column
--     last_repayment_date — no INTEGER→DATE casting needed.
--   - Filters: partner_name != 'Xtrafloat' (agents), ova != 'MOMO-ADVANCE' (overdrafts).
--
-- SOURCES:
--   devdata.account_holder_dump                              (partitioned by tbl_dt)
--   analytics.mobile_money_profiles                          (subscriber profile filter)
--   vw2_credit_v1_subscriber_day                             (physical TABLE, partitioned by run_date)
--   vw3_credit_v1_layer1_features                            (physical TABLE, partitioned by run_date)
--   analytics.momo_loan_book_tracker_loan_state_daily        (partitioned by date_key)
--
-- HOW TO USE:
--   1. Run this script once per analysis cycle.
--   2. All downstream queries reference analytics.vw_loan_eligibility_waterfall_v3.
--   3. The view file (vw_loan_eligibility_waterfall_v3.txt) does NOT need to
--      exist — this script is fully self-contained.
--
-- TO UPDATE FOR A NEW CYCLE (e.g. June 30 2026):
--   Search-replace 20260531 → 20260630 and update ALL SIX date
--   literals in the params CTE and WHERE clauses below:
--     analysis_end_date  DATE '2026-05-31'  → DATE '2026-06-30'
--     window_30d_start   DATE '2026-05-01'  → DATE '2026-06-01'
--     window_90d_start   DATE '2026-03-02'  → DATE '2026-04-01'
--     run_date           '2026-05-31'       → '2026-06-30'
--     tbl_dt             20260531           → 20260630
--     date_key           20260531           → 20260630
-- ============================================================

DROP TABLE IF EXISTS analytics.vw_loan_eligibility_waterfall_v3;

CREATE TABLE analytics.vw_loan_eligibility_waterfall_v3
WITH (
    format         = 'PARQUET',
    partitioned_by = ARRAY['analysis_dt']
)
AS

-- ── Analysis window & partition key reference ─────────────────
-- TO CHANGE THE ANALYSIS CYCLE: update ALL SIX values below consistently,
-- then search-replace each literal throughout this file.
--
--   analysis_end_date  DATE '2026-05-31'   last day of the analysis month
--   window_30d_start   DATE '2026-05-01'   first day of the analysis month
--   window_90d_start   DATE '2026-03-02'   90-day lookback start (Mar 2 – May 31 2026)
--   run_date           '2026-05-31'        partition key for vw2 and vw3 (VARCHAR) ← literal in WHERE, not in CTE
--   tbl_dt             20260531            partition key for account_holder_dump ← literal in WHERE, not in CTE
--   date_key           20260531            partition key for loan book tracker ← literal in WHERE, not in CTE
--
-- WHY partition keys are NOT in the params CTE:
--   Trino resolves partition keys at PLAN TIME from literal constants only.
--   A value derived from a CROSS JOIN CTE is resolved at RUNTIME, causing
--   Trino to scan ALL partitions before filtering. Using literals ensures
--   partition pruning fires and each table scan reads only the target partition.
WITH params AS (
    SELECT
        DATE '2026-05-31'  AS analysis_end_date,
        DATE '2026-05-01'  AS window_30d_start,
        DATE '2026-03-02'  AS window_90d_start    -- 90-day lookback: Mar 2 – May 31 2026
),

-- ── Rule 1: subscriber registration / activation dates ────────
-- Source: devdata.account_holder_dump (MOBILE MONEY, ACTIVE subscribers only).
-- tbl_dt = 20260531 is a literal constant — Trino prunes to that partition at
-- plan time, avoiding a full table scan. Both accountholder_status and
-- account_status must be 'ACTIVE'; profile must be a known subscriber profile.
-- registration_date and activation_date are VARCHAR DD-MON-YY ('18-NOV-25').
-- Sub-select parses each date column once; outer SELECT aliases and derives.
subscriber_reg AS (
    SELECT
        msisdn,
        parsed_reg_dt                              AS registration_date,
        parsed_act_dt                              AS activation_date,
        COALESCE(parsed_reg_dt, parsed_act_dt)     AS derived_reg_date
    FROM (
        SELECT
            msisdn,
            TRY(date_parse(registration_date, '%d-%b-%y')) AS parsed_reg_dt,
            TRY(date_parse(activation_date,   '%d-%b-%y')) AS parsed_act_dt
        FROM devdata.account_holder_dump
        WHERE tbl_dt               = 20260531          -- literal partition key: Trino prunes at plan time
          AND account_type         = 'MOBILE MONEY'
          AND accountholder_status = 'ACTIVE'
          AND account_status       = 'ACTIVE'
          AND profile IN (SELECT profile_name FROM analytics.mobile_money_profiles WHERE subscriber_flag = true)
    )
),

-- ── Single vw2 scan: replaces subscriber_base + mau30_activity + momo_activity ──
-- vw2 is a physical table at (event_dt, subscriber_msisdn) daily grain.
-- 90 days of history available. Conditional aggregation in one pass.
vw2_aggregated AS (
    SELECT
        d.subscriber_msisdn,
        -- subscriber_base equivalents
        MIN(d.event_dt)               AS first_activity_dt,
        MAX(d.event_dt)               AS last_activity_dt,
        COUNT(*)                      AS total_active_days,   -- one row per (subscriber, day) so COUNT(*) = COUNT(DISTINCT event_dt)
        -- Rule 2: MAU30 — days with any activity in the 30-day window
        SUM(CASE
            WHEN d.event_dt BETWEEN p.window_30d_start AND p.analysis_end_date
            THEN 1 ELSE 0
        END)                          AS active_days_last30d,
        -- Rule 3: wallet transaction count in window
        -- vw2.wallet_txn_cnt_day counts wallet_inflow + wallet_outflow + spend_behavior
        SUM(CASE
            WHEN d.event_dt BETWEEN p.window_90d_start AND p.analysis_end_date
            THEN d.wallet_txn_cnt_day ELSE 0
        END)                          AS momo_txn_cnt_30d,
        -- Rule 4: wallet inflow and outflow amounts in window
        SUM(CASE
            WHEN d.event_dt BETWEEN p.window_90d_start AND p.analysis_end_date
            THEN d.wallet_inflow_amt_day ELSE 0.0
        END)                          AS momo_inflow_30d,
        SUM(CASE
            WHEN d.event_dt BETWEEN p.window_90d_start AND p.analysis_end_date
            THEN d.wallet_outflow_amt_day ELSE 0.0
        END)                          AS momo_outflow_30d
    FROM vw2_credit_v1_subscriber_day d
    CROSS JOIN params p
    WHERE d.run_date = '2026-05-31'   -- literal partition key: Trino prunes at plan time (VARCHAR column)
    GROUP BY d.subscriber_msisdn
),

-- layer1_latest CTE removed.
-- vw3 is joined directly on (subscriber_msisdn, last_activity_dt) in eligibility_base.
-- MAX(feature_dt) in vw3 = MAX(event_dt) in vw2 = last_activity_dt computed in vw2_aggregated.

-- ── Loan book tracker base: shared by Rules 5 & 6 ────────────
-- Source: analytics.momo_loan_book_tracker_loan_state_daily
-- date_key = 20260531 is a literal constant — Trino prunes at plan time.
-- Dates (last_disbursement_date, last_repayment_date) are native DATE columns —
-- no INTEGER→DATE casting required (unlike v2).
-- cooling_off_end_date pre-computed once for reuse in loan_cooling_off_signals.
-- Exclusions:
--   partner_name = 'Xtrafloat'   — agent accounts, analysed separately.
--   ova = 'MOMO-ADVANCE'         — overdraft product, not a term loan.
loan_tracker_base AS (
    SELECT
        lt.customer_msisdn                                  AS msisdn,
        lt.loan_uid,
        lt.lifetime_disbursed_ugx,
        lt.outstanding_ugx,
        lt.lifetime_repaid_ugx,
        lt.days_aging,
        lt.loan_status,
        lt.last_disbursement_date,
        lt.last_repayment_date,
        date_add('day', 90, lt.last_repayment_date)         AS cooling_off_end_date,
        p.analysis_end_date                                  AS snapshot_date
    FROM analytics.momo_loan_book_tracker_loan_state_daily lt
    CROSS JOIN params p
    WHERE lt.date_key      = 20260531              -- literal partition key: Trino prunes at plan time
      AND lt.partner_name != 'Xtrafloat'           -- exclude agent accounts
      AND lt.ova          != 'MOMO-ADVANCE'         -- exclude overdraft product
      AND lt.customer_msisdn IN (
          SELECT subscriber_msisdn FROM vw2_credit_v1_subscriber_day WHERE run_date = '2026-05-31'
      )
),

-- ── Rule 5: overdue loans (outstanding balance, days_aging > 60) ─
-- days_aging is pre-computed in the source table — no date arithmetic needed.
-- Replacing the v2 approach (date_diff on INTEGER disbursement_date) eliminates
-- the stale loan problem: days_aging reflects current arrears status directly.
loan_overdue_signals AS (
    SELECT
        msisdn,
        approx_distinct(loan_uid)       AS overdue_loan_count,
        SUM(outstanding_ugx)            AS total_overdue_amount,
        MAX(days_aging)                 AS max_days_outstanding,
        1                               AS overdue_60d_flag
    FROM loan_tracker_base
    WHERE outstanding_ugx > 0
      AND days_aging      > 60
    GROUP BY msisdn
),

-- ── Rule 6: cooling-off after late repayment ──────────────────
-- Considers only fully repaid loans: loan_status IN ('CLOSED', 'SETTLED').
-- last_repayment_date is a native DATE column — no casting required.
-- Late repayment: date_diff(last_disbursement_date, last_repayment_date) > 30 days.
-- Cooling-off active: snapshot_date falls within 90 days of repayment.
loan_cooling_off_signals AS (
    SELECT
        msisdn,
        approx_distinct(loan_uid)                            AS cooling_off_loan_count,
        MAX(last_repayment_date)                             AS latest_late_repayment_date,
        MAX(cooling_off_end_date)                            AS latest_cooling_off_end_date,
        SUM(lifetime_disbursed_ugx)                          AS cooling_off_affected_loan_value,
        1                                                    AS cooling_off_flag
    FROM loan_tracker_base
    WHERE loan_status IN ('CLOSED', 'SETTLED')
      AND last_repayment_date IS NOT NULL
      AND date_diff('day', last_disbursement_date, last_repayment_date) > 30
      AND snapshot_date BETWEEN last_repayment_date AND cooling_off_end_date
    GROUP BY msisdn
),

-- ── Eligibility flags per subscriber ─────────────────────────
-- Inner sub-select computes momo_age_days once; outer SELECT derives rule1
-- from it instead of repeating the date_diff expression.
eligibility_base AS (
    SELECT
        *,
        CASE WHEN momo_age_days >= 180 THEN 1 ELSE 0 END  AS rule1_momo_age_180d_flag
    FROM (
        SELECT
            agg.subscriber_msisdn,
            agg.first_activity_dt,
            agg.last_activity_dt,
            agg.total_active_days,

            -- Rule 1 diagnostic columns (from devdata.account_holder_dump)
            sr.derived_reg_date,
            sr.registration_date                              AS reg_registration_date,
            sr.activation_date                               AS reg_activation_date,
            date_diff('day',
                COALESCE(sr.derived_reg_date, agg.first_activity_dt),
                p.analysis_end_date
            )                                                AS momo_age_days,

            -- Rule 2: MAU30 — active in last 30 days
            CASE WHEN COALESCE(agg.active_days_last30d, 0) >= 1 THEN 1 ELSE 0 END  AS rule2_mau30_flag,
            COALESCE(agg.active_days_last30d, 0)            AS active_days_last30d,

            -- Rule 3: >= 5 MoMo transactions in window
            CASE WHEN COALESCE(agg.momo_txn_cnt_30d, 0) >= 5
                 THEN 1 ELSE 0 END                          AS rule3_min_5txn_flag,
            COALESCE(agg.momo_txn_cnt_30d, 0)              AS momo_txn_cnt_30d,

            -- Rule 4: >= UGX 10,000 inflow or outflow in window
            CASE WHEN   COALESCE(agg.momo_inflow_30d,  0) >= 10000
                   OR   COALESCE(agg.momo_outflow_30d, 0) >= 10000
                 THEN 1 ELSE 0 END                          AS rule4_min_10k_value_flag,
            COALESCE(agg.momo_inflow_30d,  0)              AS momo_inflow_30d,
            COALESCE(agg.momo_outflow_30d, 0)              AS momo_outflow_30d,

            -- Rule 5: No active term loan overdue > 60 days
            CASE WHEN lov.overdue_60d_flag = 1 THEN 0 ELSE 1 END  AS rule5_no_overdue_60d_flag,

            -- Rule 6: Not in cooling-off period
            CASE WHEN lco.cooling_off_flag = 1 THEN 0 ELSE 1 END  AS rule6_no_cooling_off_flag,

            -- ── Financial features (from vw3) ──────────────────────
            COALESCE(l1.outstanding_exposure_amt,          0.0)   AS outstanding_exposure_amt,
            l1.repayment_ratio_30d,
            COALESCE(l1.repayment_cnt_30d,                 0)     AS repayment_cnt_30d,
            COALESCE(l1.disbursement_cnt_30d,              0)     AS disbursement_cnt_30d,
            COALESCE(l1.wallet_inflow_amt_30d,             0.0)   AS wallet_inflow_amt_30d,
            COALESCE(l1.wallet_outflow_amt_30d,            0.0)   AS wallet_outflow_amt_30d,
            COALESCE(l1.wallet_txn_cnt_30d,                0)     AS wallet_txn_cnt_30d,
            COALESCE(l1.wallet_active_days_30d,            0)     AS wallet_active_days_30d,
            COALESCE(l1.loan_disb_amt_30d,                 0.0)   AS loan_disb_amt_30d,
            COALESCE(l1.loan_repaid_amt_30d,               0.0)   AS loan_repaid_amt_30d,
            l1.days_since_last_disbursement,
            l1.days_since_last_repayment,
            l1.last_disbursement_dt,
            l1.last_repayment_dt,
            COALESCE(l1.stacked_borrowing_flag_30d,        0)     AS stacked_borrowing_flag_30d,
            COALESCE(l1.repeated_borrowing_flag_30d,       0)     AS repeated_borrowing_flag_30d,
            COALESCE(l1.active_lender_cnt_30d,             0)     AS active_lender_cnt_30d,
            COALESCE(l1.alt_credit_active_flag_30d,        0)     AS alt_credit_active_flag_30d,

            -- ── Loan portfolio signals ──────────────────────────────
            -- Source: analytics.momo_loan_book_tracker_loan_state_daily
            COALESCE(lov.overdue_loan_count,               0)     AS overdue_loan_count,
            COALESCE(lov.total_overdue_amount,             0.0)   AS total_overdue_amount,
            COALESCE(lov.max_days_outstanding,             0)     AS max_days_outstanding,
            COALESCE(lco.cooling_off_loan_count,           0)     AS cooling_off_loan_count,
            COALESCE(lco.cooling_off_affected_loan_value,  0.0)   AS cooling_off_affected_loan_value,
            lco.latest_late_repayment_date,
            lco.latest_cooling_off_end_date

        FROM vw2_aggregated agg
        CROSS JOIN params p
        LEFT JOIN subscriber_reg                      sr  ON agg.subscriber_msisdn = sr.msisdn
        LEFT JOIN vw3_credit_v1_layer1_features       l1  ON  l1.subscriber_msisdn = agg.subscriber_msisdn
                                                          AND l1.feature_dt         = agg.last_activity_dt
                                                          AND l1.run_date           = '2026-05-31'  -- literal partition key (VARCHAR column)
        LEFT JOIN loan_overdue_signals                lov ON agg.subscriber_msisdn = lov.msisdn
        LEFT JOIN loan_cooling_off_signals            lco ON agg.subscriber_msisdn = lco.msisdn
    )
),

-- ── Waterfall: cumulative eligibility after each rule ─────────
waterfall AS (
    SELECT
        *,

        CASE WHEN disbursement_cnt_30d > 0 THEN 1 ELSE 0 END  AS currently_active_borrower_flag,

        rule1_momo_age_180d_flag                               AS eligible_after_rule1,

        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
             THEN 1 ELSE 0 END                                 AS eligible_after_rule2,

        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND rule3_min_5txn_flag       = 1
             THEN 1 ELSE 0 END                                 AS eligible_after_rule3,

        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND rule3_min_5txn_flag       = 1
              AND rule4_min_10k_value_flag  = 1
             THEN 1 ELSE 0 END                                 AS eligible_after_rule4,

        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND rule3_min_5txn_flag       = 1
              AND rule4_min_10k_value_flag  = 1
              AND rule5_no_overdue_60d_flag = 1
             THEN 1 ELSE 0 END                                 AS eligible_after_rule5,

        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND rule3_min_5txn_flag       = 1
              AND rule4_min_10k_value_flag  = 1
              AND rule5_no_overdue_60d_flag = 1
              AND rule6_no_cooling_off_flag = 1
             THEN 1 ELSE 0 END                                 AS proposed_eligible_flag,

        loan_disb_amt_30d * 0.10                               AS estimated_monthly_revenue

    FROM eligibility_base
)

SELECT
    subscriber_msisdn,
    first_activity_dt,
    last_activity_dt,
    total_active_days,
    -- Rule 1 registration diagnostics
    derived_reg_date,
    reg_registration_date,
    reg_activation_date,
    momo_age_days,
    -- Rule flags
    rule1_momo_age_180d_flag,
    rule2_mau30_flag,
    rule3_min_5txn_flag,
    rule4_min_10k_value_flag,
    rule5_no_overdue_60d_flag,
    rule6_no_cooling_off_flag,
    -- Waterfall eligibility
    eligible_after_rule1,
    eligible_after_rule2,
    eligible_after_rule3,
    eligible_after_rule4,
    eligible_after_rule5,
    proposed_eligible_flag,
    currently_active_borrower_flag,
    -- Activity metrics
    active_days_last30d,
    momo_txn_cnt_30d,
    momo_inflow_30d,
    momo_outflow_30d,
    -- Financial features (vw3)
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
    -- Risk behaviour flags (vw3)
    stacked_borrowing_flag_30d,
    repeated_borrowing_flag_30d,
    active_lender_cnt_30d,
    alt_credit_active_flag_30d,
    -- Loan portfolio signals (analytics.momo_loan_book_tracker_loan_state_daily)
    overdue_loan_count,
    total_overdue_amount,
    max_days_outstanding,
    cooling_off_loan_count,
    cooling_off_affected_loan_value,
    latest_late_repayment_date,
    latest_cooling_off_end_date,
    -- Revenue estimate
    estimated_monthly_revenue,
    -- Partition column — matches analysis_end_date in params CTE
    DATE '2026-05-31'  AS analysis_dt

FROM waterfall;

-- ── Verification ──────────────────────────────────────────────
SELECT
    COUNT(*)                            AS total_rows,
    SUM(proposed_eligible_flag)         AS eligible_total,
    SUM(eligible_after_rule1)           AS after_rule1,
    SUM(eligible_after_rule2)           AS after_rule2,
    SUM(eligible_after_rule3)           AS after_rule3,
    SUM(eligible_after_rule4)           AS after_rule4,
    SUM(eligible_after_rule5)           AS after_rule5,
    SUM(currently_active_borrower_flag) AS active_borrowers,
    ROUND(SUM(estimated_monthly_revenue), 0) AS est_monthly_revenue
FROM analytics.vw_loan_eligibility_waterfall_v3
WHERE analysis_dt = DATE '2026-05-31';
