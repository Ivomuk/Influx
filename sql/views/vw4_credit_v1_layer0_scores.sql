-- Dimension 2 + Dimension 5 upgrade.
-- Scoring rules now consume multi-window repayment ratios, trend features,
-- anti-gaming flags, and the upgraded identity_confidence_score_v2.
-- All score names retain the _v1_rule suffix for continuity with downstream consumers.

CREATE OR REPLACE VIEW vw4_credit_v1_layer0_scores AS
-- Cast feature_dt to DATE once; Presto/Hive returns DATE columns from Hive
-- tables as BIGINT (days since epoch).
WITH vw3_data AS (
    SELECT
        CAST(feature_dt AS DATE) AS feature_dt,
        subscriber_msisdn,
        outstanding_exposure_amt,
        active_lender_cnt_30d,
        disbursement_cnt_30d,
        days_since_last_disbursement,
        repayment_ratio_30d,
        repayment_cnt_30d,
        days_since_last_repayment,
        repayment_ratio_14d,
        repayment_ratio_60d,
        repayment_ratio_90d,
        repayment_ratio_trend_7d,
        wallet_inflow_amt_30d,
        wallet_inflow_amt_90d,
        wallet_outflow_amt_30d,
        spend_amt_30d,
        wallet_txn_cnt_30d,
        wallet_active_days_30d,
        wallet_active_days_7d,
        wallet_active_days_14d,
        wallet_active_days_90d,
        wallet_inflow_trend_7d_vs_90d,
        post_loan_wallet_activity_change_ratio,
        stacked_borrowing_flag_30d,
        repeated_borrowing_flag_30d,
        alt_credit_active_flag_30d,
        active_lender_days_30d,
        loan_disb_amt_30d,
        loan_repaid_amt_30d,
        timing_manipulation_flag,
        suspicious_repayment_jump_flag,
        loan_cycling_flag,
        sim_age_days,
        sim_age_cohort,
        has_inflow_and_spend_flag,
        identity_confidence_score_v2
    FROM vw3_credit_v1_layer1_features
),

base_metrics AS (
    SELECT
        feature_dt,
        subscriber_msisdn,

        -- Core 30d exposure and repayment
        outstanding_exposure_amt,
        active_lender_cnt_30d,
        disbursement_cnt_30d,
        days_since_last_disbursement,
        repayment_ratio_30d,
        repayment_cnt_30d,
        days_since_last_repayment,

        -- Multi-window repayment and trend (Dimension 2)
        repayment_ratio_14d,
        repayment_ratio_60d,
        repayment_ratio_90d,
        repayment_ratio_trend_7d,

        -- Wallet activity
        wallet_inflow_amt_30d,
        wallet_inflow_amt_90d,
        wallet_outflow_amt_30d,
        spend_amt_30d,
        wallet_txn_cnt_30d,
        wallet_active_days_30d,
        wallet_active_days_7d,
        wallet_active_days_14d,
        wallet_active_days_90d,
        wallet_inflow_trend_7d_vs_90d,

        -- Post-loan activity change
        post_loan_wallet_activity_change_ratio,

        -- Behaviour flags
        stacked_borrowing_flag_30d,
        repeated_borrowing_flag_30d,
        alt_credit_active_flag_30d,
        active_lender_days_30d,
        loan_disb_amt_30d,
        loan_repaid_amt_30d,

        -- Anti-gaming flags (Dimension 5)
        timing_manipulation_flag,
        suspicious_repayment_jump_flag,
        loan_cycling_flag,

        -- Identity maturity (Dimension 5)
        sim_age_days,
        sim_age_cohort,
        has_inflow_and_spend_flag,
        identity_confidence_score_v2,

        -- Exposure-to-inflow ratio
        CASE
            WHEN wallet_inflow_amt_30d > 0 THEN outstanding_exposure_amt / wallet_inflow_amt_30d
            ELSE NULL
        END AS exposure_to_inflow_ratio

    FROM vw3_data
),

first_level_scores AS (
    SELECT
        *,

        -- ------------------------------------------------------------------
        -- Expected repayment probability
        -- Now uses 60d/90d ratios as corroborating signal and penalises
        -- a deteriorating 7-day trend (even if 30d ratio looks acceptable).
        -- ------------------------------------------------------------------
        CASE
            WHEN repayment_ratio_30d >= 0.90
                 AND COALESCE(repayment_ratio_60d, repayment_ratio_30d) >= 0.80
                 AND COALESCE(exposure_to_inflow_ratio, 0.0) < 0.50
                 AND wallet_active_days_30d >= 8
                 AND COALESCE(repayment_ratio_trend_7d, 0.0) >= -0.10   -- not sharply declining
                THEN 0.90
            WHEN repayment_ratio_30d >= 0.75
                 AND COALESCE(repayment_ratio_60d, repayment_ratio_30d) >= 0.65
                 AND COALESCE(exposure_to_inflow_ratio, 0.0) < 1.00
                THEN 0.75
            WHEN repayment_ratio_30d >= 0.50
                 AND COALESCE(repayment_ratio_trend_7d, 0.0) >= -0.20   -- not collapsing
                THEN 0.55
            ELSE 0.25
        END AS expected_repayment_probability_v1_rule,

        -- ------------------------------------------------------------------
        -- Expected credit loss rate
        -- Uses 90d ratio where available for a more stable estimate.
        -- ------------------------------------------------------------------
        CASE
            WHEN repayment_ratio_30d IS NULL                             THEN 0.50
            WHEN COALESCE(repayment_ratio_90d, repayment_ratio_30d) >= 0.90 THEN 0.05
            WHEN COALESCE(repayment_ratio_90d, repayment_ratio_30d) >= 0.75 THEN 0.15
            WHEN repayment_ratio_30d >= 0.50                             THEN 0.30
            ELSE 0.60
        END AS expected_credit_loss_rate_v1_rule,

        -- ------------------------------------------------------------------
        -- Churn / cooling-off probability
        -- Now also elevated if wallet inflow is declining short-term vs long-term.
        -- ------------------------------------------------------------------
        CASE
            WHEN post_loan_wallet_activity_change_ratio IS NULL          THEN 0.30
            WHEN post_loan_wallet_activity_change_ratio <= -0.50         THEN 0.80
            WHEN post_loan_wallet_activity_change_ratio <= -0.20         THEN 0.60
            WHEN COALESCE(wallet_inflow_trend_7d_vs_90d, 1.0) < 0.50    THEN 0.50
            ELSE 0.20
        END AS churn_cooling_probability_v1_rule,

        -- ------------------------------------------------------------------
        -- Expected future transaction margin score
        -- Uses 90d wallet figures where available for a less noisy estimate.
        -- ------------------------------------------------------------------
        CASE
            WHEN COALESCE(wallet_inflow_amt_90d, wallet_inflow_amt_30d * 3) / 3.0
                 + COALESCE(spend_amt_30d, 0) >= 50000                   THEN 0.90
            WHEN COALESCE(wallet_inflow_amt_90d, wallet_inflow_amt_30d * 3) / 3.0
                 + COALESCE(spend_amt_30d, 0) >= 20000                   THEN 0.65
            ELSE 0.30
        END AS expected_future_transaction_margin_score_v1_rule,

        -- ------------------------------------------------------------------
        -- Expected treatment cost score (elevated for anti-gaming patterns)
        -- ------------------------------------------------------------------
        CASE
            WHEN loan_cycling_flag = 1                                    THEN 0.85
            WHEN stacked_borrowing_flag_30d = 1
                 OR repeated_borrowing_flag_30d = 1                       THEN 0.70
            WHEN COALESCE(exposure_to_inflow_ratio, 0.0) > 1.00          THEN 0.60
            WHEN suspicious_repayment_jump_flag = 1
                 OR timing_manipulation_flag = 1                          THEN 0.55
            ELSE 0.20
        END AS expected_treatment_cost_score_v1_rule,

        -- ------------------------------------------------------------------
        -- Identity confidence: now uses the maturity-based v2 score from vw3
        -- rather than a binary null check.
        -- ------------------------------------------------------------------
        identity_confidence_score_v2 AS identity_confidence_score_v1_rule,

        -- ------------------------------------------------------------------
        -- Debt Stress Index: 5 original components (20 pts each) plus
        -- 3 new anti-gaming penalty points capped at 20 extra points total.
        -- Maximum DSI remains 100 after clipping.
        -- ------------------------------------------------------------------
        LEAST(
            (
                20 * CASE WHEN COALESCE(exposure_to_inflow_ratio, 0.0) > 1.00 THEN 1 ELSE 0 END +
                20 * CASE WHEN active_lender_cnt_30d >= 2                      THEN 1 ELSE 0 END +
                20 * CASE WHEN repeated_borrowing_flag_30d = 1                 THEN 1 ELSE 0 END +
                20 * CASE WHEN COALESCE(post_loan_wallet_activity_change_ratio, 0.0) <= -0.50 THEN 1 ELSE 0 END +
                20 * CASE WHEN COALESCE(repayment_ratio_30d, 0.0) < 0.50      THEN 1 ELSE 0 END +
                -- Anti-gaming additions (capped together at 20 pts)
                10 * CASE WHEN timing_manipulation_flag = 1                    THEN 1 ELSE 0 END +
                 5 * CASE WHEN suspicious_repayment_jump_flag = 1              THEN 1 ELSE 0 END +
                 5 * CASE WHEN loan_cycling_flag = 1                           THEN 1 ELSE 0 END
            ),
            100
        ) AS debt_stress_index_v1

    FROM base_metrics
),

second_level_scores AS (
    SELECT
        *,

        -- ------------------------------------------------------------------
        -- Fraud / abuse risk: anti-gaming flags now directly elevate the score.
        -- ------------------------------------------------------------------
        CASE
            WHEN loan_cycling_flag = 1
                 AND (repeated_borrowing_flag_30d = 1 OR stacked_borrowing_flag_30d = 1) THEN 0.90
            WHEN loan_cycling_flag = 1                                                    THEN 0.75
            WHEN repeated_borrowing_flag_30d = 1 AND stacked_borrowing_flag_30d = 1      THEN 0.80
            WHEN stacked_borrowing_flag_30d = 1                                           THEN 0.60
            WHEN suspicious_repayment_jump_flag = 1 OR timing_manipulation_flag = 1      THEN 0.55
            WHEN alt_credit_active_flag_30d = 1                                           THEN 0.40
            ELSE 0.10
        END AS fraud_abuse_risk_score_v1_rule,

        -- ------------------------------------------------------------------
        -- Behaviour consistency: now uses multi-window activity days.
        -- A subscriber active across 7d, 14d, and 30d windows is more consistent.
        -- ------------------------------------------------------------------
        CASE
            WHEN wallet_active_days_30d >= 10
                 AND wallet_active_days_14d >= 5
                 AND wallet_active_days_7d  >= 2
                 AND wallet_inflow_amt_30d > 0
                 AND spend_amt_30d > 0                                   THEN 0.90
            WHEN wallet_active_days_30d >= 10
                 AND wallet_inflow_amt_30d > 0
                 AND spend_amt_30d > 0                                   THEN 0.85
            WHEN wallet_active_days_30d >= 5                             THEN 0.60
            ELSE 0.25
        END AS behavior_consistency_score_v1_rule

    FROM first_level_scores
),

final_scores AS (
    SELECT
        feature_dt,
        subscriber_msisdn,
        expected_repayment_probability_v1_rule,
        expected_credit_loss_rate_v1_rule,
        churn_cooling_probability_v1_rule,
        expected_future_transaction_margin_score_v1_rule,
        expected_treatment_cost_score_v1_rule,

        (
            expected_future_transaction_margin_score_v1_rule
            * (1 - churn_cooling_probability_v1_rule)
            * behavior_consistency_score_v1_rule
        ) AS customer_lifetime_value_contribution_v1_rule,

        debt_stress_index_v1,
        fraud_abuse_risk_score_v1_rule,
        behavior_consistency_score_v1_rule,
        identity_confidence_score_v1_rule,

        -- State transition probabilities (unchanged logic, improved inputs)
        CASE WHEN debt_stress_index_v1 >= 80 THEN 0.80 ELSE 0.10 END AS prob_distressed_v1_rule,
        CASE WHEN debt_stress_index_v1 BETWEEN 60 AND 79 THEN 0.70 ELSE 0.15 END AS prob_at_risk_v1_rule,
        CASE WHEN churn_cooling_probability_v1_rule >= 0.70 THEN 0.75 ELSE 0.10 END AS prob_cooling_off_v1_rule,
        CASE
            WHEN expected_repayment_probability_v1_rule >= 0.80
                 AND debt_stress_index_v1 < 40                          THEN 0.80
            ELSE 0.20
        END AS prob_healthy_v1_rule,

        -- Pass-through anti-gaming flags for downstream policy use
        timing_manipulation_flag,
        suspicious_repayment_jump_flag,
        loan_cycling_flag,

        -- Pass-through identity maturity for policy and fairness use
        sim_age_days,
        sim_age_cohort,
        has_inflow_and_spend_flag,

        -- Pass-through trend features for snapshot / calibration use
        repayment_ratio_trend_7d,
        wallet_inflow_trend_7d_vs_90d

    FROM second_level_scores
)

SELECT
    feature_dt,
    subscriber_msisdn,
    expected_repayment_probability_v1_rule,
    expected_credit_loss_rate_v1_rule,
    churn_cooling_probability_v1_rule,
    expected_future_transaction_margin_score_v1_rule,
    expected_treatment_cost_score_v1_rule,
    customer_lifetime_value_contribution_v1_rule,
    debt_stress_index_v1,
    fraud_abuse_risk_score_v1_rule,
    behavior_consistency_score_v1_rule,
    identity_confidence_score_v1_rule,
    prob_distressed_v1_rule,
    prob_at_risk_v1_rule,
    prob_cooling_off_v1_rule,
    prob_healthy_v1_rule,
    timing_manipulation_flag,
    suspicious_repayment_jump_flag,
    loan_cycling_flag,
    sim_age_days,
    sim_age_cohort,
    has_inflow_and_spend_flag,
    repayment_ratio_trend_7d,
    wallet_inflow_trend_7d_vs_90d
FROM final_scores
;
