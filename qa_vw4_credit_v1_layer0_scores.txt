-- QA suite for vw4_credit_v1_layer0_scores
-- Validates all 10 Layer 0 scores and 4 state-transition probabilities.
-- Each query returns rows only when an anomaly is detected.
-- Zero rows = pass.
--
-- Score catalogue (10 scores):
--   1. expected_repayment_probability_v1_rule       [0, 1]
--   2. expected_credit_loss_rate_v1_rule            [0, 1]
--   3. churn_cooling_probability_v1_rule            [0, 1]
--   4. expected_future_transaction_margin_score_v1_rule [0, 1]
--   5. expected_treatment_cost_score_v1_rule        [0, 1]
--   6. customer_lifetime_value_contribution_v1_rule [0, 1]  (derived)
--   7. debt_stress_index_v1                         [0, 100]
--   8. fraud_abuse_risk_score_v1_rule               [0, 1]
--   9. behavior_consistency_score_v1_rule           [0, 1]
--  10. identity_confidence_score_v1_rule            [0, 1]

-- ---------------------------------------------------------------------------
-- QA-VW4-01: Probability scores outside [0, 1]
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    expected_repayment_probability_v1_rule,
    expected_credit_loss_rate_v1_rule,
    churn_cooling_probability_v1_rule,
    expected_future_transaction_margin_score_v1_rule,
    expected_treatment_cost_score_v1_rule,
    customer_lifetime_value_contribution_v1_rule,
    fraud_abuse_risk_score_v1_rule,
    behavior_consistency_score_v1_rule,
    identity_confidence_score_v1_rule
FROM vw4_credit_v1_layer0_scores
WHERE
    expected_repayment_probability_v1_rule                NOT BETWEEN 0.0 AND 1.0
    OR expected_credit_loss_rate_v1_rule                  NOT BETWEEN 0.0 AND 1.0
    OR churn_cooling_probability_v1_rule                  NOT BETWEEN 0.0 AND 1.0
    OR expected_future_transaction_margin_score_v1_rule   NOT BETWEEN 0.0 AND 1.0
    OR expected_treatment_cost_score_v1_rule              NOT BETWEEN 0.0 AND 1.0
    OR customer_lifetime_value_contribution_v1_rule       NOT BETWEEN 0.0 AND 1.0
    OR fraud_abuse_risk_score_v1_rule                     NOT BETWEEN 0.0 AND 1.0
    OR behavior_consistency_score_v1_rule                 NOT BETWEEN 0.0 AND 1.0
    OR identity_confidence_score_v1_rule                  NOT BETWEEN 0.0 AND 1.0
;

-- ---------------------------------------------------------------------------
-- QA-VW4-02: Debt Stress Index outside [0, 100]
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    debt_stress_index_v1
FROM vw4_credit_v1_layer0_scores
WHERE debt_stress_index_v1 NOT BETWEEN 0 AND 100
;

-- ---------------------------------------------------------------------------
-- QA-VW4-03: NULL scores in required output columns
-- All 10 scores must be non-null; vw4 supplies defaults for every branch.
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    expected_repayment_probability_v1_rule,
    expected_credit_loss_rate_v1_rule,
    debt_stress_index_v1,
    fraud_abuse_risk_score_v1_rule,
    identity_confidence_score_v1_rule
FROM vw4_credit_v1_layer0_scores
WHERE
    expected_repayment_probability_v1_rule IS NULL
    OR expected_credit_loss_rate_v1_rule   IS NULL
    OR debt_stress_index_v1                IS NULL
    OR fraud_abuse_risk_score_v1_rule      IS NULL
    OR identity_confidence_score_v1_rule   IS NULL
;

-- ---------------------------------------------------------------------------
-- QA-VW4-04: CLV derived formula sanity check
-- customer_lifetime_value_contribution =
--   expected_future_transaction_margin_score
--   * (1 - churn_cooling_probability)
--   * behavior_consistency_score
-- Allow 0.01 floating-point tolerance.
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    customer_lifetime_value_contribution_v1_rule,
    expected_future_transaction_margin_score_v1_rule
    * (1.0 - churn_cooling_probability_v1_rule)
    * behavior_consistency_score_v1_rule AS clv_recomputed,
    ABS(
        customer_lifetime_value_contribution_v1_rule
        - expected_future_transaction_margin_score_v1_rule
          * (1.0 - churn_cooling_probability_v1_rule)
          * behavior_consistency_score_v1_rule
    ) AS clv_delta
FROM vw4_credit_v1_layer0_scores
WHERE
    ABS(
        customer_lifetime_value_contribution_v1_rule
        - expected_future_transaction_margin_score_v1_rule
          * (1.0 - churn_cooling_probability_v1_rule)
          * behavior_consistency_score_v1_rule
    ) > 0.01
;

-- ---------------------------------------------------------------------------
-- QA-VW4-05: Anti-gaming flag pass-throughs are still binary
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    timing_manipulation_flag,
    suspicious_repayment_jump_flag,
    loan_cycling_flag
FROM vw4_credit_v1_layer0_scores
WHERE
    timing_manipulation_flag      NOT IN (0, 1)
    OR suspicious_repayment_jump_flag NOT IN (0, 1)
    OR loan_cycling_flag              NOT IN (0, 1)
;

-- ---------------------------------------------------------------------------
-- QA-VW4-06: Duplicate (feature_dt, subscriber_msisdn)
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    COUNT(*) AS duplicate_count
FROM vw4_credit_v1_layer0_scores
GROUP BY feature_dt, subscriber_msisdn
HAVING COUNT(*) > 1
;

-- ---------------------------------------------------------------------------
-- QA-VW4-07: Rows present in vw3 but missing from vw4 (join coverage)
-- ---------------------------------------------------------------------------
SELECT
    l1.feature_dt,
    l1.subscriber_msisdn
FROM vw3_credit_v1_layer1_features l1
LEFT JOIN vw4_credit_v1_layer0_scores l0
    ON l1.feature_dt = l0.feature_dt
    AND l1.subscriber_msisdn = l0.subscriber_msisdn
WHERE l0.subscriber_msisdn IS NULL
;
