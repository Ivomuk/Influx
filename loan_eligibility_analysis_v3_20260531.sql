-- ============================================================
-- loan_eligibility_analysis_v3_20260531.sql
--
-- PURPOSE: SQL equivalents of the Python impact analysis script.
--   Run these queries against the persisted results table to avoid
--   loading 20M rows into local memory.
--
-- SOURCE TABLE: analytics.vw_loan_eligibility_waterfall_v3
-- ANALYSIS DATE: 2026-05-31
-- DATA WINDOW: Mar 2 – May 31 2026 (90-day window, Rules 3 & 4)
--
-- DIFFERENCE vs v2 analysis:
--   Loan data sourced from analytics.momo_loan_book_tracker_loan_state_daily.
--   Rule 5 uses days_aging > 60 (pre-computed); Rule 6 uses loan_status
--   IN ('CLOSED','SETTLED') with native DATE repayment dates.
--   Results: 7,465,831 eligible (35.9%) vs 2,201,459 (10.6%) in v2.
-- ============================================================


-- ── 1. ELIGIBILITY WATERFALL ──────────────────────────────────
-- Rule-by-rule funnel: how many subscribers pass each cumulative gate.

SELECT
    COUNT(*)                            AS total_subscribers,
    SUM(eligible_after_rule1)           AS pass_rule1_age_180d,
    SUM(eligible_after_rule2)           AS pass_rule2_mau30,
    SUM(eligible_after_rule3)           AS pass_rule3_min5txn,
    SUM(eligible_after_rule4)           AS pass_rule4_min10k,
    SUM(eligible_after_rule5)           AS pass_rule5_no_overdue,
    SUM(proposed_eligible_flag)         AS final_eligible,
    ROUND(100.0 * SUM(proposed_eligible_flag) / COUNT(*), 1) AS eligible_pct
FROM analytics.vw_loan_eligibility_waterfall_v3;


-- ── 2. EXECUTIVE SUMMARY ──────────────────────────────────────
-- Active borrowers (baseline) vs proposed eligible: customers,
-- revenue, loan value, and repayment quality.

SELECT
    SUM(CASE WHEN currently_active_borrower_flag = 1 THEN 1                          END) AS active_borrowers,
    SUM(CASE WHEN proposed_eligible_flag         = 1 THEN 1                          END) AS proposed_eligible,
    SUM(CASE WHEN currently_active_borrower_flag = 1 THEN estimated_monthly_revenue  END) AS rev_active,
    SUM(CASE WHEN proposed_eligible_flag         = 1 THEN estimated_monthly_revenue  END) AS rev_proposed,
    SUM(CASE WHEN currently_active_borrower_flag = 1 THEN loan_disb_amt_30d          END) AS loan_val_active,
    SUM(CASE WHEN proposed_eligible_flag         = 1 THEN loan_disb_amt_30d          END) AS loan_val_proposed,
    ROUND(AVG(CASE WHEN currently_active_borrower_flag = 1 THEN repayment_ratio_30d  END), 3) AS avg_repay_ratio_active,
    ROUND(AVG(CASE WHEN proposed_eligible_flag         = 1 THEN repayment_ratio_30d  END), 3) AS avg_repay_ratio_proposed,
    ROUND(AVG(CASE WHEN currently_active_borrower_flag = 1 THEN outstanding_exposure_amt END), 0) AS avg_exposure_active,
    ROUND(AVG(CASE WHEN proposed_eligible_flag         = 1 THEN outstanding_exposure_amt END), 0) AS avg_exposure_proposed
FROM analytics.vw_loan_eligibility_waterfall_v3;


-- ── 3. DROP-OFF BY RULE ───────────────────────────────────────
-- Incremental drop at each gate: subscribers who pass all prior
-- rules but fail this one.

SELECT
    SUM(CASE WHEN rule1_momo_age_180d_flag  = 0                                       THEN 1 END) AS fail_rule1_age,
    SUM(CASE WHEN eligible_after_rule1 = 1  AND rule2_mau30_flag         = 0          THEN 1 END) AS fail_rule2_mau30,
    SUM(CASE WHEN eligible_after_rule2 = 1  AND rule3_min_5txn_flag      = 0          THEN 1 END) AS fail_rule3_txn,
    SUM(CASE WHEN eligible_after_rule3 = 1  AND rule4_min_10k_value_flag = 0          THEN 1 END) AS fail_rule4_value,
    SUM(CASE WHEN eligible_after_rule4 = 1  AND rule5_no_overdue_60d_flag = 0         THEN 1 END) AS fail_rule5_overdue,
    SUM(CASE WHEN eligible_after_rule5 = 1  AND rule6_no_cooling_off_flag = 0         THEN 1 END) AS fail_rule6_cooling_off
FROM analytics.vw_loan_eligibility_waterfall_v3;


-- ── 4. STANDALONE RULE IMPACT ─────────────────────────────────
-- Each rule tested in isolation: customers failing it, and the
-- loan value / revenue associated with those customers.

SELECT
    rule,
    COUNT(*)                                    AS customers_failing,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS fail_pct,
    ROUND(SUM(loan_disb_amt_30d), 0)            AS loan_value_at_risk,
    ROUND(SUM(estimated_monthly_revenue), 0)    AS revenue_at_risk,
    ROUND(AVG(repayment_ratio_30d), 3)          AS avg_repay_ratio_failing
FROM (
    SELECT 'Rule 1 — MoMo age < 180d'              AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule1_momo_age_180d_flag   = 0
    UNION ALL
    SELECT 'Rule 2 — not MAU30'                    AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule2_mau30_flag            = 0
    UNION ALL
    SELECT 'Rule 3 — < 5 txns in 90d'             AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule3_min_5txn_flag         = 0
    UNION ALL
    SELECT 'Rule 4 — < UGX 10k inflow/outflow'    AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule4_min_10k_value_flag    = 0
    UNION ALL
    SELECT 'Rule 5 — overdue > 60d'               AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule5_no_overdue_60d_flag   = 0
    UNION ALL
    SELECT 'Rule 6 — in cooling-off period'        AS rule, loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3 WHERE rule6_no_cooling_off_flag   = 0
)
GROUP BY rule
ORDER BY rule;


-- ── 5. SENSITIVITY SCENARIOS ──────────────────────────────────
-- Three threshold scenarios applied to the same subscriber base.
-- Proposed (agreed rules) | Less Strict | More Strict

SELECT
    scenario,
    SUM(eligible)                               AS eligible_customers,
    ROUND(100.0 * SUM(eligible) / COUNT(*), 1) AS eligible_pct,
    ROUND(SUM(CASE WHEN eligible = 1 THEN loan_disb_amt_30d END), 0)         AS total_loan_value,
    ROUND(SUM(CASE WHEN eligible = 1 THEN estimated_monthly_revenue END), 0) AS est_revenue,
    ROUND(AVG(CASE WHEN eligible = 1 THEN repayment_ratio_30d END), 3)       AS avg_repay_ratio
FROM (
    -- Proposed: Rule 3 >= 5 txns, Rule 4 >= UGX 10,000, Rule 5 > 60d, Rule 6 > 30d
    SELECT 'Proposed (agreed rules)' AS scenario,
        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND momo_txn_cnt_30d         >= 5
              AND (momo_inflow_30d >= 10000 OR momo_outflow_30d >= 10000)
              AND rule5_no_overdue_60d_flag = 1
              AND rule6_no_cooling_off_flag = 1
             THEN 1 ELSE 0 END AS eligible,
        loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3

    UNION ALL

    -- Less Strict: Rule 3 >= 3 txns, Rule 4 >= UGX 5,000
    SELECT 'Less Strict' AS scenario,
        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND momo_txn_cnt_30d         >= 3
              AND (momo_inflow_30d >= 5000 OR momo_outflow_30d >= 5000)
              AND rule5_no_overdue_60d_flag = 1
              AND rule6_no_cooling_off_flag = 1
             THEN 1 ELSE 0 END AS eligible,
        loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3

    UNION ALL

    -- More Strict: Rule 3 >= 10 txns, Rule 4 >= UGX 20,000
    SELECT 'More Strict' AS scenario,
        CASE WHEN rule1_momo_age_180d_flag = 1
              AND rule2_mau30_flag          = 1
              AND momo_txn_cnt_30d         >= 10
              AND (momo_inflow_30d >= 20000 OR momo_outflow_30d >= 20000)
              AND rule5_no_overdue_60d_flag = 1
              AND rule6_no_cooling_off_flag = 1
             THEN 1 ELSE 0 END AS eligible,
        loan_disb_amt_30d, estimated_monthly_revenue, repayment_ratio_30d
    FROM analytics.vw_loan_eligibility_waterfall_v3
)
GROUP BY scenario
ORDER BY scenario;


-- ── 6. RISK SEGMENTATION ──────────────────────────────────────
-- Pass-all-rules vs fail-one-or-more: financial and repayment
-- behaviour comparison including loan portfolio signals.

SELECT
    CASE WHEN proposed_eligible_flag = 1 THEN 'Pass all rules'
         ELSE 'Fail one or more rules'
    END                                                          AS segment,
    COUNT(*)                                                     AS subscribers,
    ROUND(SUM(loan_disb_amt_30d), 0)                            AS total_loan_value,
    ROUND(AVG(loan_disb_amt_30d), 0)                            AS avg_loan_size,
    ROUND(SUM(estimated_monthly_revenue), 0)                    AS est_monthly_revenue,
    ROUND(AVG(repayment_ratio_30d), 3)                          AS avg_repay_ratio,
    ROUND(AVG(outstanding_exposure_amt), 0)                     AS avg_exposure,
    ROUND(AVG(days_since_last_repayment), 0)                    AS avg_days_since_repay,
    ROUND(AVG(stacked_borrowing_flag_30d)  * 100, 1)            AS stacked_borrow_pct,
    ROUND(AVG(repeated_borrowing_flag_30d) * 100, 1)            AS repeated_borrow_pct,
    ROUND(AVG(wallet_inflow_amt_30d), 0)                        AS avg_wallet_inflow,
    SUM(CASE WHEN overdue_loan_count      > 0 THEN 1 END)       AS customers_with_overdue,
    ROUND(SUM(total_overdue_amount), 0)                         AS total_overdue_exposure,
    SUM(CASE WHEN cooling_off_loan_count  > 0 THEN 1 END)       AS customers_in_cooling_off,
    ROUND(SUM(cooling_off_affected_loan_value), 0)              AS cooling_off_loan_value
FROM analytics.vw_loan_eligibility_waterfall_v3
GROUP BY 1
ORDER BY 1;


-- ── 7. BORROWER TYPE BREAKDOWN (eligible only) ────────────────
-- Stacked vs repeat vs new borrowers among the proposed eligible pool.

SELECT
    CASE WHEN stacked_borrowing_flag_30d  = 1 THEN 'Stacked borrower'
         WHEN repeated_borrowing_flag_30d = 1 THEN 'Repeat borrower'
         ELSE 'New / single borrower'
    END                                         AS borrower_type,
    COUNT(*)                                    AS subscribers,
    ROUND(AVG(momo_txn_cnt_30d), 1)            AS avg_txns_90d,
    ROUND(AVG(momo_inflow_30d), 0)             AS avg_inflow,
    ROUND(AVG(loan_disb_amt_30d), 0)           AS avg_disbursed,
    ROUND(AVG(repayment_ratio_30d), 3)         AS avg_repay_ratio,
    ROUND(SUM(estimated_monthly_revenue), 0)   AS est_revenue
FROM analytics.vw_loan_eligibility_waterfall_v3
WHERE proposed_eligible_flag = 1
GROUP BY 1
ORDER BY subscribers DESC;
