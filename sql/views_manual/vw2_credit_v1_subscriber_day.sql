-- Manual INSERT script for vw2_credit_v1_subscriber_day.
-- Depends on vw1_credit_v1_normalized_events — run vw1 first.
--
-- Aggregates all 90 days loaded in vw1 (2026-03-03 → 2026-05-31) into
-- daily subscriber rollups under a single run_date = '2026-05-31' partition.
-- No outer date filter — all event_dt values from vw1 are aggregated.

SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw2_credit_v1_subscriber_day
SELECT t.*, '2026-05-31' AS run_date
FROM (
    WITH daily_rollup AS (
        SELECT
            CAST(event_dt AS DATE)          AS event_dt,
            subscriber_msisdn,

            SUM(disbursement_amt)           AS loan_disb_amt_day,
            SUM(repayment_amt)              AS loan_repaid_amt_day,
            SUM(wallet_inflow_amt)          AS wallet_inflow_amt_day,
            SUM(wallet_outflow_amt)         AS wallet_outflow_amt_day,
            SUM(spend_amt)                  AS spend_amt_day,
            SUM(savings_amt)                AS savings_amt_day,

            COUNT_IF(event_family = 'loan_disbursement') AS loan_disb_cnt_day,
            COUNT_IF(event_family = 'loan_repayment') AS loan_repay_cnt_day,
            COUNT_IF(event_family IN ('wallet_inflow', 'wallet_outflow', 'spend_behavior', 'savings')) AS wallet_txn_cnt_day,

            COUNT(DISTINCT CASE
                WHEN lender_family_v1 IS NOT NULL THEN lender_family_v1
                ELSE NULL
            END) AS lender_family_cnt_day,

            MAX(CASE WHEN event_family = 'loan_disbursement' THEN 1 ELSE 0 END) AS had_disbursement_day,
            MAX(CASE WHEN event_family = 'loan_repayment'    THEN 1 ELSE 0 END) AS had_repayment_day,
            MAX(CASE WHEN alt_credit_signal_flag = 1         THEN 1 ELSE 0 END) AS alt_credit_signal_flag_day
        FROM vw1_credit_v1_normalized_events
        GROUP BY
            event_dt,
            subscriber_msisdn
    )
    SELECT
        event_dt,
        subscriber_msisdn,
        loan_disb_amt_day,
        loan_repaid_amt_day,
        wallet_inflow_amt_day,
        wallet_outflow_amt_day,
        spend_amt_day,
        savings_amt_day,
        loan_disb_cnt_day,
        loan_repay_cnt_day,
        wallet_txn_cnt_day,
        lender_family_cnt_day,
        had_disbursement_day,
        had_repayment_day,
        alt_credit_signal_flag_day
    FROM daily_rollup
) t
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';
