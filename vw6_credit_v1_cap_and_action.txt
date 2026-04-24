CREATE OR REPLACE VIEW vw6_credit_v1_cap_and_action AS
WITH base AS (
    SELECT
        r.feature_dt,
        r.subscriber_msisdn,
        r.primary_reason_code,

        l1.outstanding_exposure_amt,
        l1.wallet_inflow_amt_30d,
        l1.repayment_ratio_30d,
        l1.days_since_last_disbursement,
        l1.disbursement_cnt_30d,

        l0.expected_repayment_probability_v1_rule,
        l0.expected_credit_loss_rate_v1_rule,
        l0.churn_cooling_probability_v1_rule,
        l0.debt_stress_index_v1
    FROM vw5_credit_v1_reason_codes r
    INNER JOIN vw3_credit_v1_layer1_features l1
        ON r.feature_dt = l1.feature_dt
       AND r.subscriber_msisdn = l1.subscriber_msisdn
    INNER JOIN vw4_credit_v1_layer0_scores l0
        ON r.feature_dt = l0.feature_dt
       AND r.subscriber_msisdn = l0.subscriber_msisdn
),
action_rules AS (
    SELECT
        feature_dt,
        subscriber_msisdn,
        primary_reason_code,
        wallet_inflow_amt_30d,
        repayment_ratio_30d,
        debt_stress_index_v1,

        CASE
            WHEN primary_reason_code IN (
                'LOW_IDENTITY_CONFIDENCE',
                'HIGH_FRAUD_ABUSE_RISK',
                'SEVERE_DSI',
                'RECENT_DISBURSEMENT_NO_REPAYMENT'
            ) THEN 'RESTRICT'
            WHEN primary_reason_code IN (
                'HIGH_EXPOSURE_TO_INFLOW',
                'VERY_LOW_REPAYMENT_RATIO',
                'STACKED_BORROWING',
                'REPEATED_BORROWING',
                'HIGH_COOLING_RISK'
            ) THEN 'REDUCE'
            ELSE 'MAINTAIN_OR_INCREASE'
        END AS recommended_action
    FROM base
),
credit_caps AS (
    SELECT
        a.feature_dt,
        a.subscriber_msisdn,
        a.primary_reason_code,
        a.recommended_action,

        CASE
            WHEN b.wallet_inflow_amt_30d IS NULL OR b.wallet_inflow_amt_30d <= 0 THEN 0
            WHEN b.repayment_ratio_30d < 0.25 THEN 0
            WHEN b.debt_stress_index_v1 >= 80 THEN 0
            WHEN b.debt_stress_index_v1 >= 60 THEN LEAST(b.wallet_inflow_amt_30d * 0.20, 5000)
            WHEN b.repayment_ratio_30d < 0.75 THEN LEAST(b.wallet_inflow_amt_30d * 0.30, 10000)
            ELSE LEAST(b.wallet_inflow_amt_30d * 0.50, 20000)
        END AS conservative_credit_limit_v1
    FROM action_rules a
    INNER JOIN base b
        ON a.feature_dt = b.feature_dt
       AND a.subscriber_msisdn = b.subscriber_msisdn
)
SELECT
    feature_dt,
    subscriber_msisdn,
    primary_reason_code,
    recommended_action,
    conservative_credit_limit_v1
FROM credit_caps
;