-- Backfill script for vw2_credit_v1_subscriber_day.
-- Reads directly from analytics.momo_tran_loc_mapping_v2 (bypasses vw1 storage).
-- Applies vw1 normalisation inline, then aggregates to vw2 daily-rollup format.
-- All partitions land under run_date = '2026-05-31'.
--
-- Run batches in order. Batch 1 uses OVERWRITE to initialise the partition;
-- all subsequent batches use APPEND.
--
-- Coverage: 2026-03-03 → 2026-05-31  (90 days of history as at 2026-05-31)
-- Batch 1 : 20260303 – 20260309  (OVERWRITE)
-- Batch 2 : 20260310 – 20260316  (APPEND)
-- Batch 3 : 20260317 – 20260323  (APPEND)
-- … (add further batches as needed through to 20260531)

-- ============================================================
-- BATCH 1 : 20260303 – 20260309
-- ============================================================
SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw2_credit_v1_subscriber_day
SELECT t.*, '2026-05-31' AS run_date
FROM (
    WITH raw_base AS (
        SELECT
            CAST(date_parse(CAST(date_key AS VARCHAR), '%Y%m%d') AS DATE) AS event_dt,
            CAST(service_name     AS VARCHAR) AS service_name,
            CAST(sub_service_name AS VARCHAR) AS sub_service_name,
            CAST(from_msisdn      AS VARCHAR) AS from_msisdn,
            CAST(to_msisdn        AS VARCHAR) AS to_msisdn,
            CAST(replace(CAST(txn_value AS VARCHAR), ',', '') AS DOUBLE) AS txn_value_num
        FROM analytics.momo_tran_loc_mapping_v2
        WHERE CAST(date_key AS VARCHAR) BETWEEN '20260303' AND '20260309'
          AND NOT (
                (service_name = 'Xtrafloat'   AND sub_service_name = 'Fee (Xtrafloat)')
             OR (service_name = 'Clinic Pesa' AND sub_service_name = 'Revenue Share (Clinic Pesa)')
             OR  service_name = 'Fees from Airtime reversals'
          )
    ),
    normalized AS (
        SELECT
            event_dt,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN 'loan_disbursement'
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN 'loan_repayment'
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN 'savings'
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN 'savings'
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN 'savings'
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN 'savings'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN 'wallet_inflow'
                WHEN service_name = 'Remittance Inbound'                                                    THEN 'wallet_inflow'
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN 'wallet_inflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN 'wallet_inflow'
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN 'wallet_inflow'
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN 'wallet_inflow'
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN 'wallet_outflow'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN 'wallet_outflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN 'wallet_outflow'
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN 'spend_behavior'
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN 'alt_credit_signal'
                ELSE 'other_non_loan'
            END AS event_family,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN to_msisdn
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN from_msisdn
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN from_msisdn
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN from_msisdn
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN from_msisdn
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN to_msisdn
                WHEN service_name = 'Remittance Inbound'                                                    THEN to_msisdn
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN to_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN to_msisdn
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN to_msisdn
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN to_msisdn
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN from_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN from_msisdn
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN from_msisdn
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN COALESCE(to_msisdn, from_msisdn)
                ELSE COALESCE(to_msisdn, from_msisdn)
            END AS subscriber_msisdn,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN service_name
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN service_name
                WHEN service_name IN ('MoPesa', 'Mokash', 'MoMo advance', 'XtraCash', 'Jumo')            THEN service_name
                ELSE NULL
            END AS lender_family_v1,
            CASE WHEN lower(sub_service_name) LIKE '%disbursement%' THEN txn_value_num ELSE 0.0 END AS disbursement_amt,
            CASE WHEN lower(sub_service_name) LIKE '%repayment%'    THEN txn_value_num ELSE 0.0 END AS repayment_amt,
            CASE
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN txn_value_num
                WHEN service_name = 'Remittance Inbound'                                                    THEN txn_value_num
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN txn_value_num
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS wallet_inflow_amt,
            CASE
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN txn_value_num
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN txn_value_num
                ELSE 0.0
            END AS wallet_outflow_amt,
            CASE
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                     AND lower(sub_service_name) NOT LIKE '%disbursement%'
                     AND lower(sub_service_name) NOT LIKE '%repayment%'
                    THEN txn_value_num
                ELSE 0.0
            END AS spend_amt,
            CASE WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN 1 ELSE 0 END AS alt_credit_signal_flag,
            CASE
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN txn_value_num
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS savings_amt
        FROM raw_base
    )
    SELECT
        event_dt,
        subscriber_msisdn,
        SUM(disbursement_amt)   AS loan_disb_amt_day,
        SUM(repayment_amt)      AS loan_repaid_amt_day,
        SUM(wallet_inflow_amt)  AS wallet_inflow_amt_day,
        SUM(wallet_outflow_amt) AS wallet_outflow_amt_day,
        SUM(spend_amt)          AS spend_amt_day,
        SUM(savings_amt)        AS savings_amt_day,
        COUNT_IF(event_family = 'loan_disbursement')                                                       AS loan_disb_cnt_day,
        COUNT_IF(event_family = 'loan_repayment')                                                          AS loan_repay_cnt_day,
        COUNT_IF(event_family IN ('wallet_inflow', 'wallet_outflow', 'spend_behavior', 'savings'))         AS wallet_txn_cnt_day,
        COUNT(DISTINCT CASE WHEN lender_family_v1 IS NOT NULL THEN lender_family_v1 ELSE NULL END)        AS lender_family_cnt_day,
        MAX(CASE WHEN event_family = 'loan_disbursement' THEN 1 ELSE 0 END)                               AS had_disbursement_day,
        MAX(CASE WHEN event_family = 'loan_repayment'    THEN 1 ELSE 0 END)                               AS had_repayment_day,
        MAX(CASE WHEN alt_credit_signal_flag = 1         THEN 1 ELSE 0 END)                               AS alt_credit_signal_flag_day
    FROM normalized
    WHERE subscriber_msisdn IS NOT NULL
    GROUP BY event_dt, subscriber_msisdn
) t
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';

-- ============================================================
-- BATCH 2 : 20260310 – 20260316
-- ============================================================

INSERT INTO hive.credit_engine.vw2_credit_v1_subscriber_day
SELECT t.*, '2026-05-31' AS run_date
FROM (
    WITH raw_base AS (
        SELECT
            CAST(date_parse(CAST(date_key AS VARCHAR), '%Y%m%d') AS DATE) AS event_dt,
            CAST(service_name     AS VARCHAR) AS service_name,
            CAST(sub_service_name AS VARCHAR) AS sub_service_name,
            CAST(from_msisdn      AS VARCHAR) AS from_msisdn,
            CAST(to_msisdn        AS VARCHAR) AS to_msisdn,
            CAST(replace(CAST(txn_value AS VARCHAR), ',', '') AS DOUBLE) AS txn_value_num
        FROM analytics.momo_tran_loc_mapping_v2
        WHERE CAST(date_key AS VARCHAR) BETWEEN '20260310' AND '20260316'
          AND NOT (
                (service_name = 'Xtrafloat'   AND sub_service_name = 'Fee (Xtrafloat)')
             OR (service_name = 'Clinic Pesa' AND sub_service_name = 'Revenue Share (Clinic Pesa)')
             OR  service_name = 'Fees from Airtime reversals'
          )
    ),
    normalized AS (
        SELECT
            event_dt,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN 'loan_disbursement'
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN 'loan_repayment'
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN 'savings'
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN 'savings'
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN 'savings'
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN 'savings'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN 'wallet_inflow'
                WHEN service_name = 'Remittance Inbound'                                                    THEN 'wallet_inflow'
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN 'wallet_inflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN 'wallet_inflow'
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN 'wallet_inflow'
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN 'wallet_inflow'
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN 'wallet_outflow'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN 'wallet_outflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN 'wallet_outflow'
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN 'spend_behavior'
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN 'alt_credit_signal'
                ELSE 'other_non_loan'
            END AS event_family,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN to_msisdn
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN from_msisdn
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN from_msisdn
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN from_msisdn
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN from_msisdn
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN to_msisdn
                WHEN service_name = 'Remittance Inbound'                                                    THEN to_msisdn
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN to_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN to_msisdn
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN to_msisdn
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN to_msisdn
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN from_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN from_msisdn
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN from_msisdn
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN COALESCE(to_msisdn, from_msisdn)
                ELSE COALESCE(to_msisdn, from_msisdn)
            END AS subscriber_msisdn,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN service_name
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN service_name
                WHEN service_name IN ('MoPesa', 'Mokash', 'MoMo advance', 'XtraCash', 'Jumo')            THEN service_name
                ELSE NULL
            END AS lender_family_v1,
            CASE WHEN lower(sub_service_name) LIKE '%disbursement%' THEN txn_value_num ELSE 0.0 END AS disbursement_amt,
            CASE WHEN lower(sub_service_name) LIKE '%repayment%'    THEN txn_value_num ELSE 0.0 END AS repayment_amt,
            CASE
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN txn_value_num
                WHEN service_name = 'Remittance Inbound'                                                    THEN txn_value_num
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN txn_value_num
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS wallet_inflow_amt,
            CASE
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN txn_value_num
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN txn_value_num
                ELSE 0.0
            END AS wallet_outflow_amt,
            CASE
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                     AND lower(sub_service_name) NOT LIKE '%disbursement%'
                     AND lower(sub_service_name) NOT LIKE '%repayment%'
                    THEN txn_value_num
                ELSE 0.0
            END AS spend_amt,
            CASE WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN 1 ELSE 0 END AS alt_credit_signal_flag,
            CASE
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN txn_value_num
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS savings_amt
        FROM raw_base
    )
    SELECT
        event_dt,
        subscriber_msisdn,
        SUM(disbursement_amt)   AS loan_disb_amt_day,
        SUM(repayment_amt)      AS loan_repaid_amt_day,
        SUM(wallet_inflow_amt)  AS wallet_inflow_amt_day,
        SUM(wallet_outflow_amt) AS wallet_outflow_amt_day,
        SUM(spend_amt)          AS spend_amt_day,
        SUM(savings_amt)        AS savings_amt_day,
        COUNT_IF(event_family = 'loan_disbursement')                                                       AS loan_disb_cnt_day,
        COUNT_IF(event_family = 'loan_repayment')                                                          AS loan_repay_cnt_day,
        COUNT_IF(event_family IN ('wallet_inflow', 'wallet_outflow', 'spend_behavior', 'savings'))         AS wallet_txn_cnt_day,
        COUNT(DISTINCT CASE WHEN lender_family_v1 IS NOT NULL THEN lender_family_v1 ELSE NULL END)        AS lender_family_cnt_day,
        MAX(CASE WHEN event_family = 'loan_disbursement' THEN 1 ELSE 0 END)                               AS had_disbursement_day,
        MAX(CASE WHEN event_family = 'loan_repayment'    THEN 1 ELSE 0 END)                               AS had_repayment_day,
        MAX(CASE WHEN alt_credit_signal_flag = 1         THEN 1 ELSE 0 END)                               AS alt_credit_signal_flag_day
    FROM normalized
    WHERE subscriber_msisdn IS NOT NULL
    GROUP BY event_dt, subscriber_msisdn
) t
;

-- ============================================================
-- BATCH 3 : 20260317 – 20260323
-- ============================================================

INSERT INTO hive.credit_engine.vw2_credit_v1_subscriber_day
SELECT t.*, '2026-05-31' AS run_date
FROM (
    WITH raw_base AS (
        SELECT
            CAST(date_parse(CAST(date_key AS VARCHAR), '%Y%m%d') AS DATE) AS event_dt,
            CAST(service_name     AS VARCHAR) AS service_name,
            CAST(sub_service_name AS VARCHAR) AS sub_service_name,
            CAST(from_msisdn      AS VARCHAR) AS from_msisdn,
            CAST(to_msisdn        AS VARCHAR) AS to_msisdn,
            CAST(replace(CAST(txn_value AS VARCHAR), ',', '') AS DOUBLE) AS txn_value_num
        FROM analytics.momo_tran_loc_mapping_v2
        WHERE CAST(date_key AS VARCHAR) BETWEEN '20260317' AND '20260323'
          AND NOT (
                (service_name = 'Xtrafloat'   AND sub_service_name = 'Fee (Xtrafloat)')
             OR (service_name = 'Clinic Pesa' AND sub_service_name = 'Revenue Share (Clinic Pesa)')
             OR  service_name = 'Fees from Airtime reversals'
          )
    ),
    normalized AS (
        SELECT
            event_dt,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN 'loan_disbursement'
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN 'loan_repayment'
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN 'savings'
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN 'savings'
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN 'savings'
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN 'savings'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN 'wallet_inflow'
                WHEN service_name = 'Remittance Inbound'                                                    THEN 'wallet_inflow'
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN 'wallet_inflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN 'wallet_inflow'
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN 'wallet_inflow'
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN 'wallet_inflow'
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN 'wallet_outflow'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN 'wallet_outflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN 'wallet_outflow'
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN 'spend_behavior'
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN 'alt_credit_signal'
                ELSE 'other_non_loan'
            END AS event_family,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN to_msisdn
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN from_msisdn
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN from_msisdn
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN from_msisdn
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN from_msisdn
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN to_msisdn
                WHEN service_name = 'Remittance Inbound'                                                    THEN to_msisdn
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN to_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN to_msisdn
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN to_msisdn
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN to_msisdn
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN from_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN from_msisdn
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')                                   THEN from_msisdn
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)'     THEN COALESCE(to_msisdn, from_msisdn)
                ELSE COALESCE(to_msisdn, from_msisdn)
            END AS subscriber_msisdn,
            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%'                                        THEN service_name
                WHEN lower(sub_service_name) LIKE '%repayment%'                                           THEN service_name
                WHEN service_name IN ('MoPesa', 'Mokash', 'MoMo advance', 'XtraCash', 'Jumo')            THEN service_name
                ELSE NULL
            END AS lender_family_v1,
            CASE WHEN lower(sub_service_name) LIKE '%disbursement%' THEN txn_value_num ELSE 0.0 END AS disbursement_amt,
            CASE WHEN lower(sub_service_name) LIKE '%repayment%'    THEN txn_value_num ELSE 0.0 END AS repayment_amt,
            CASE
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN txn_value_num
                WHEN service_name = 'Remittance Inbound'                                                    THEN txn_value_num
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN txn_value_num
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS wallet_inflow_amt,
            CASE
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN txn_value_num
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN txn_value_num
                ELSE 0.0
            END AS wallet_outflow_amt,
            CASE
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                     AND lower(sub_service_name) NOT LIKE '%disbursement%'
                     AND lower(sub_service_name) NOT LIKE '%repayment%'
                    THEN txn_value_num
                ELSE 0.0
            END AS spend_amt,
            CASE WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN 1 ELSE 0 END AS alt_credit_signal_flag,
            CASE
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN txn_value_num
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS savings_amt
        FROM raw_base
    )
    SELECT
        event_dt,
        subscriber_msisdn,
        SUM(disbursement_amt)   AS loan_disb_amt_day,
        SUM(repayment_amt)      AS loan_repaid_amt_day,
        SUM(wallet_inflow_amt)  AS wallet_inflow_amt_day,
        SUM(wallet_outflow_amt) AS wallet_outflow_amt_day,
        SUM(spend_amt)          AS spend_amt_day,
        SUM(savings_amt)        AS savings_amt_day,
        COUNT_IF(event_family = 'loan_disbursement')                                                       AS loan_disb_cnt_day,
        COUNT_IF(event_family = 'loan_repayment')                                                          AS loan_repay_cnt_day,
        COUNT_IF(event_family IN ('wallet_inflow', 'wallet_outflow', 'spend_behavior', 'savings'))         AS wallet_txn_cnt_day,
        COUNT(DISTINCT CASE WHEN lender_family_v1 IS NOT NULL THEN lender_family_v1 ELSE NULL END)        AS lender_family_cnt_day,
        MAX(CASE WHEN event_family = 'loan_disbursement' THEN 1 ELSE 0 END)                               AS had_disbursement_day,
        MAX(CASE WHEN event_family = 'loan_repayment'    THEN 1 ELSE 0 END)                               AS had_repayment_day,
        MAX(CASE WHEN alt_credit_signal_flag = 1         THEN 1 ELSE 0 END)                               AS alt_credit_signal_flag_day
    FROM normalized
    WHERE subscriber_msisdn IS NOT NULL
    GROUP BY event_dt, subscriber_msisdn
) t
;
