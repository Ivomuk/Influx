-- QA suite for vw3_credit_v1_layer1_features
-- Validates the multi-window feature engineering layer.
-- Each query returns rows only when an anomaly is detected.
-- Zero rows = pass.

-- ---------------------------------------------------------------------------
-- QA-VW3-01: Repayment ratios outside [0, 1]
-- Ratios are computed as repaid / disbursed; valid range is 0.0 to 1.0.
-- NULL is acceptable (no disbursement in window).
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    repayment_ratio_30d,
    repayment_ratio_14d,
    repayment_ratio_60d,
    repayment_ratio_90d
FROM vw3_credit_v1_layer1_features
WHERE
    repayment_ratio_30d NOT BETWEEN 0 AND 1
    OR repayment_ratio_14d NOT BETWEEN 0 AND 1
    OR repayment_ratio_60d NOT BETWEEN 0 AND 1
    OR repayment_ratio_90d NOT BETWEEN 0 AND 1
;

-- ---------------------------------------------------------------------------
-- QA-VW3-02: Anti-gaming flags outside {0, 1}
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    timing_manipulation_flag,
    suspicious_repayment_jump_flag,
    loan_cycling_flag
FROM vw3_credit_v1_layer1_features
WHERE
    timing_manipulation_flag        NOT IN (0, 1)
    OR suspicious_repayment_jump_flag   NOT IN (0, 1)
    OR loan_cycling_flag                NOT IN (0, 1)
;

-- ---------------------------------------------------------------------------
-- QA-VW3-03: Negative outstanding exposure
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    outstanding_exposure_amt
FROM vw3_credit_v1_layer1_features
WHERE outstanding_exposure_amt < 0
;

-- ---------------------------------------------------------------------------
-- QA-VW3-04: Wallet active days exceed their window size
-- active_days_Nd cannot exceed N by definition.
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    wallet_active_days_7d,
    wallet_active_days_14d,
    wallet_active_days_30d,
    wallet_active_days_60d,
    wallet_active_days_90d
FROM vw3_credit_v1_layer1_features
WHERE
    wallet_active_days_7d  > 7
    OR wallet_active_days_14d > 14
    OR wallet_active_days_30d > 30
    OR wallet_active_days_60d > 60
    OR wallet_active_days_90d > 90
;

-- ---------------------------------------------------------------------------
-- QA-VW3-05: SIM age days is negative
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    sim_age_days
FROM vw3_credit_v1_layer1_features
WHERE sim_age_days < 0
;

-- ---------------------------------------------------------------------------
-- QA-VW3-06: sim_age_cohort contains unexpected values
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    sim_age_cohort
FROM vw3_credit_v1_layer1_features
WHERE sim_age_cohort NOT IN ('mature', 'developing', 'thin')
;

-- ---------------------------------------------------------------------------
-- QA-VW3-07: identity_confidence_score_v2 outside [0, 1]
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    identity_confidence_score_v2
FROM vw3_credit_v1_layer1_features
WHERE identity_confidence_score_v2 NOT BETWEEN 0.0 AND 1.0
;

-- ---------------------------------------------------------------------------
-- QA-VW3-08: Duplicate (feature_dt, subscriber_msisdn)
-- ---------------------------------------------------------------------------
SELECT
    feature_dt,
    subscriber_msisdn,
    COUNT(*) AS duplicate_count
FROM vw3_credit_v1_layer1_features
GROUP BY feature_dt, subscriber_msisdn
HAVING COUNT(*) > 1
;
