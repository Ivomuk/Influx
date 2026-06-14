-- Manual INSERT script for vw5_credit_v1_reason_codes.
-- Depends on vw3 and vw4 — run both first.
-- Replace YYYY-MM-DD with the execution date before running.

SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw5_credit_v1_reason_codes
SELECT t.*, 'YYYY-MM-DD' AS run_date
FROM (
    -- Cast feature_dt to DATE in both source tables; Presto/Hive returns DATE
    -- columns from Hive tables as BIGINT (days since epoch).
    WITH vw3_data AS (
        SELECT
            CAST(feature_dt AS DATE) AS feature_dt,
            subscriber_msisdn,
            outstanding_exposure_amt,
            active_lender_cnt_30d,
            disbursement_cnt_30d,
            days_since_last_disbursement,
            repayment_ratio_30d,
            post_loan_wallet_activity_change_ratio,
            stacked_borrowing_flag_30d,
            repeated_borrowing_flag_30d,
            wallet_inflow_amt_30d
        FROM vw3_credit_v1_layer1_features
    ),

    vw4_data AS (
        SELECT
            CAST(feature_dt AS DATE) AS feature_dt,
            subscriber_msisdn,
            expected_repayment_probability_v1_rule,
            expected_credit_loss_rate_v1_rule,
            churn_cooling_probability_v1_rule,
            debt_stress_index_v1,
            fraud_abuse_risk_score_v1_rule,
            identity_confidence_score_v1_rule
        FROM vw4_credit_v1_layer0_scores
    ),

    joined AS (
        SELECT
            l1.feature_dt,
            l1.subscriber_msisdn,
            l1.outstanding_exposure_amt,
            l1.active_lender_cnt_30d,
            l1.disbursement_cnt_30d,
            l1.days_since_last_disbursement,
            l1.repayment_ratio_30d,
            l1.post_loan_wallet_activity_change_ratio,
            l1.stacked_borrowing_flag_30d,
            l1.repeated_borrowing_flag_30d,
            l1.wallet_inflow_amt_30d,
            l0.expected_repayment_probability_v1_rule,
            l0.expected_credit_loss_rate_v1_rule,
            l0.churn_cooling_probability_v1_rule,
            l0.debt_stress_index_v1,
            l0.fraud_abuse_risk_score_v1_rule,
            l0.identity_confidence_score_v1_rule
        FROM vw3_data l1
        INNER JOIN vw4_data l0
            ON l1.feature_dt = l0.feature_dt
           AND l1.subscriber_msisdn = l0.subscriber_msisdn
    ),
    base_rules AS (
        SELECT
            feature_dt,
            subscriber_msisdn,
            outstanding_exposure_amt,
            active_lender_cnt_30d,
            disbursement_cnt_30d,
            days_since_last_disbursement,
            repayment_ratio_30d,
            post_loan_wallet_activity_change_ratio,
            stacked_borrowing_flag_30d,
            repeated_borrowing_flag_30d,
            wallet_inflow_amt_30d,
            expected_repayment_probability_v1_rule,
            expected_credit_loss_rate_v1_rule,
            churn_cooling_probability_v1_rule,
            debt_stress_index_v1,
            fraud_abuse_risk_score_v1_rule,
            identity_confidence_score_v1_rule,
            CASE
                WHEN identity_confidence_score_v1_rule < 0.70 THEN 'LOW_IDENTITY_CONFIDENCE'
                WHEN fraud_abuse_risk_score_v1_rule >= 0.70 THEN 'HIGH_FRAUD_ABUSE_RISK'
                WHEN debt_stress_index_v1 >= 80 THEN 'SEVERE_DSI'
                WHEN repayment_ratio_30d IS NOT NULL AND repayment_ratio_30d < 0.25 THEN 'VERY_LOW_REPAYMENT_RATIO'
                WHEN wallet_inflow_amt_30d > 0 AND outstanding_exposure_amt / wallet_inflow_amt_30d > 1.00 THEN 'HIGH_EXPOSURE_TO_INFLOW'
                WHEN stacked_borrowing_flag_30d = 1 THEN 'STACKED_BORROWING'
                WHEN repeated_borrowing_flag_30d = 1 THEN 'REPEATED_BORROWING'
                WHEN post_loan_wallet_activity_change_ratio IS NOT NULL
                     AND post_loan_wallet_activity_change_ratio <= -0.50 THEN 'HIGH_COOLING_RISK'
                WHEN days_since_last_disbursement IS NOT NULL
                     AND days_since_last_disbursement <= 7
                     AND COALESCE(repayment_ratio_30d, 0.0) = 0.0 THEN 'RECENT_DISBURSEMENT_NO_REPAYMENT'
                ELSE 'ELIGIBLE_BASELINE'
            END AS primary_reason_code
        FROM joined
    )
    SELECT
        feature_dt,
        subscriber_msisdn,
        primary_reason_code
    FROM base_rules
) t
WHERE t.feature_dt = DATE 'YYYY-MM-DD'
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';
