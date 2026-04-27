CREATE OR REPLACE VIEW qa_vw1_credit_v1_signed_amount_sanity AS
WITH base AS (
    SELECT
        event_family,
        txn_value_num,
        signed_amount
    FROM vw1_credit_v1_normalized_events
),
aggregated AS (
    SELECT
        event_family,
        COUNT(*) AS total_rows,
        COUNT_IF(txn_value_num <= 0) AS non_positive_txn_value_cnt,
        COUNT_IF(
            event_family = 'loan_disbursement'
            AND signed_amount <= 0
        ) AS bad_loan_disbursement_sign_cnt,
        COUNT_IF(
            event_family = 'loan_repayment'
            AND signed_amount >= 0
        ) AS bad_loan_repayment_sign_cnt,
        COUNT_IF(
            event_family = 'wallet_inflow'
            AND signed_amount <= 0
        ) AS bad_wallet_inflow_sign_cnt,
        COUNT_IF(
            event_family IN ('wallet_outflow', 'spend_behavior')
            AND signed_amount >= 0
        ) AS bad_wallet_outflow_sign_cnt,
        COUNT_IF(
            event_family = 'savings'
            AND signed_amount >= 0
        ) AS bad_savings_sign_cnt
    FROM base
    GROUP BY event_family
)
SELECT
    event_family,
    total_rows,
    non_positive_txn_value_cnt,
    bad_loan_disbursement_sign_cnt,
    bad_loan_repayment_sign_cnt,
    bad_wallet_inflow_sign_cnt,
    bad_wallet_outflow_sign_cnt,
    bad_savings_sign_cnt
FROM aggregated
;
