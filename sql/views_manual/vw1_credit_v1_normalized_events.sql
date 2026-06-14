-- Manual INSERT script for vw1_credit_v1_normalized_events.
-- Run after deployment/pipeline_view_tables.sql has created the table.
--
-- Step 1: Replace YYYY-MM-DD with the execution date (e.g. 2026-04-26).
-- Step 2: Run in Presto connected to hive.credit_engine.
-- Step 3: Run scripts in dependency order: vw1 → vw2 → vw3 → vw4 → vw5 → vw6.
--
-- The SET SESSION lines overwrite the existing partition for the date so
-- re-running the same date is safe (idempotent).

SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw1_credit_v1_normalized_events
SELECT t.*, 'YYYY-MM-DD' AS run_date
FROM (
    WITH raw_base AS (
        SELECT
            CAST(date_parse(CAST(date_key AS varchar), '%Y%m%d') AS date) AS event_dt,
            inserted_dt,
            fid,
            service_name,
            sub_service_name,
            instruct_hdr_type,
            from_msisdn,
            to_msisdn,
            CAST(replace(CAST(txn_value AS varchar), ',', '') AS double) AS txn_value_num,
            from_profile,
            to_profile,
            from_sp,
            to_sp
        FROM analytics.momo_tran_loc_mapping_v2
        WHERE date_key BETWEEN 20260401 AND 20260430
          AND NOT (
                (service_name = 'Xtrafloat'               AND sub_service_name = 'Fee (Xtrafloat)')
             OR (service_name = 'Clinic Pesa'              AND sub_service_name = 'Revenue Share (Clinic Pesa)')
             OR  service_name = 'Fees from Airtime reversals'
          )
    ),
    base_events AS (
        SELECT
            event_dt,
            inserted_dt,
            fid,
            service_name,
            sub_service_name,
            instruct_hdr_type,
            from_msisdn,
            to_msisdn,
            txn_value_num,
            from_profile,
            to_profile,
            from_sp,
            to_sp,

            CASE
                -- Loan patterns (identified by sub_service_name) — unchanged
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN 'loan_disbursement'
                WHEN lower(sub_service_name) LIKE '%repayment%'    THEN 'loan_repayment'

                -- Savings deposits (subscriber moves money from wallet into a savings product)
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN 'savings'
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN 'savings'
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN 'savings'
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN 'savings'

                -- Wallet inflows (money arriving into the subscriber's wallet)
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN 'wallet_inflow'
                WHEN service_name = 'Remittance Inbound'                                                    THEN 'wallet_inflow'
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN 'wallet_inflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN 'wallet_inflow'
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN 'wallet_inflow'
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN 'wallet_inflow'

                -- Wallet outflows (money leaving the subscriber's wallet)
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN 'wallet_outflow'
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN 'wallet_outflow'
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN 'wallet_outflow'

                -- Spend behaviour (discretionary consumption)
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                    THEN 'spend_behavior'

                -- Alternative credit signal (third-party credit product usage)
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN 'alt_credit_signal'

                ELSE 'other_non_loan'
            END AS event_family,

            CASE
                -- Loan patterns — unchanged
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN to_msisdn
                WHEN lower(sub_service_name) LIKE '%repayment%'    THEN from_msisdn

                -- Savings deposits: subscriber is the depositor
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN from_msisdn
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN from_msisdn
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN from_msisdn
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN from_msisdn

                -- Wallet inflows: subscriber is the receiver
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN to_msisdn
                WHEN service_name = 'Remittance Inbound'                                                    THEN to_msisdn
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN to_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN to_msisdn
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN to_msisdn
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN to_msisdn

                -- Wallet outflows: subscriber is the sender
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN from_msisdn
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN from_msisdn
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN from_msisdn

                -- Spend: subscriber is the spender
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                    THEN from_msisdn

                -- Alt credit signal
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN COALESCE(to_msisdn, from_msisdn)

                ELSE COALESCE(to_msisdn, from_msisdn)
            END AS subscriber_msisdn,

            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN from_msisdn
                WHEN lower(sub_service_name) LIKE '%repayment%'    THEN to_msisdn
                ELSE NULL
            END AS loan_counterparty_msisdn,

            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN service_name
                WHEN lower(sub_service_name) LIKE '%repayment%'    THEN service_name
                WHEN service_name IN ('MoPesa', 'Mokash', 'MoMo advance', 'XtraCash', 'Jumo') THEN service_name
                ELSE NULL
            END AS lender_family_v1,

            CASE
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN txn_value_num
                ELSE 0.0
            END AS disbursement_amt,

            CASE
                WHEN lower(sub_service_name) LIKE '%repayment%' THEN txn_value_num
                ELSE 0.0
            END AS repayment_amt,

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

            CASE
                WHEN service_name = 'MoMo advance' AND sub_service_name = 'Access Fee (MoMo advance)' THEN 1
                ELSE 0
            END AS alt_credit_signal_flag,

            CASE
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN txn_value_num
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN txn_value_num
                ELSE 0.0
            END AS savings_amt,

            CASE
                -- Loans
                WHEN lower(sub_service_name) LIKE '%disbursement%' THEN txn_value_num
                WHEN lower(sub_service_name) LIKE '%repayment%'    THEN -1 * txn_value_num
                -- Wallet inflows (positive)
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'Airtel-MTN'                     THEN txn_value_num
                WHEN service_name = 'Remittance Inbound'                                                    THEN txn_value_num
                WHEN service_name = 'MoPesa'       AND sub_service_name = 'Savings Withdraw (MoPesa)'      THEN txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Savings Withdraw (Clinic Pesa)' THEN txn_value_num
                WHEN service_name = 'Mokash'       AND sub_service_name = 'Savings Withdraw (Mokash)'      THEN txn_value_num
                WHEN service_name = 'XENO'         AND sub_service_name = 'Savings Withdraw (XENO)'        THEN txn_value_num
                -- Wallet outflows (negative)
                WHEN service_name IN ('Cash Out', 'Remittance Outbound')                                    THEN -1 * txn_value_num
                WHEN service_name = 'P2P Off-net'  AND sub_service_name = 'MTN-Airtel'                     THEN -1 * txn_value_num
                WHEN service_name = 'Clinic Pesa'  AND sub_service_name = 'Facilities Payments (Clinic Pesa)' THEN -1 * txn_value_num
                -- Spend (negative)
                WHEN service_name IN ('Airtime', 'Bundles', 'Momo Pay Merchants', 'Debit/Merchant',
                                      'P2P On-net', 'Payment/Collection')
                    THEN -1 * txn_value_num
                -- Savings deposits (negative — money leaving wallet into savings)
                WHEN service_name = 'Clinic Pesa' AND sub_service_name = 'Savings Deposit (Clinic Pesa)' THEN -1 * txn_value_num
                WHEN service_name = 'Mokash'      AND sub_service_name = 'Savings Deposit (Mokash)'      THEN -1 * txn_value_num
                WHEN service_name = 'MoPesa'      AND sub_service_name = 'Savings Deposit (MoPesa)'      THEN -1 * txn_value_num
                WHEN service_name = 'XENO'        AND sub_service_name = 'Savings Deposit (XENO)'        THEN -1 * txn_value_num
                ELSE 0.0
            END AS signed_amount
        FROM raw_base
    )
    SELECT
        event_dt,
        inserted_dt,
        fid,
        service_name,
        sub_service_name,
        instruct_hdr_type,
        from_msisdn,
        to_msisdn,
        txn_value_num,
        from_profile,
        to_profile,
        from_sp,
        to_sp,
        event_family,
        subscriber_msisdn,
        loan_counterparty_msisdn,
        lender_family_v1,
        disbursement_amt,
        repayment_amt,
        wallet_inflow_amt,
        wallet_outflow_amt,
        spend_amt,
        alt_credit_signal_flag,
        savings_amt,
        signed_amount
    FROM base_events
    WHERE subscriber_msisdn IS NOT NULL
) t
WHERE t.event_dt = DATE 'YYYY-MM-DD'
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';
