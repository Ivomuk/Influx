-- QA suite for vw2_credit_v1_subscriber_day
-- Validates the daily rollup layer before it feeds into feature engineering.
-- Each query returns rows only when an anomaly is detected.
-- Zero rows = pass.

-- ---------------------------------------------------------------------------
-- QA-VW2-01: Duplicate (subscriber_msisdn, event_dt) pairs
-- One row per subscriber per day is the contract downstream relies on.
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt,
    COUNT(*) AS duplicate_count
FROM vw2_credit_v1_subscriber_day
GROUP BY subscriber_msisdn, event_dt
HAVING COUNT(*) > 1
;

-- ---------------------------------------------------------------------------
-- QA-VW2-02: Negative monetary amounts
-- Disbursements, repayments, inflows, outflows, and spend must all be >= 0.
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt,
    loan_disb_amt_day,
    loan_repaid_amt_day,
    wallet_inflow_amt_day,
    wallet_outflow_amt_day,
    spend_amt_day
FROM vw2_credit_v1_subscriber_day
WHERE
    loan_disb_amt_day    < 0
    OR loan_repaid_amt_day   < 0
    OR wallet_inflow_amt_day < 0
    OR wallet_outflow_amt_day < 0
    OR spend_amt_day         < 0
;

-- ---------------------------------------------------------------------------
-- QA-VW2-03: Negative or fractional event counts
-- Count columns must be non-negative integers.
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt,
    loan_disb_cnt_day,
    loan_repay_cnt_day,
    wallet_txn_cnt_day,
    lender_family_cnt_day
FROM vw2_credit_v1_subscriber_day
WHERE
    loan_disb_cnt_day    < 0
    OR loan_repay_cnt_day    < 0
    OR wallet_txn_cnt_day    < 0
    OR lender_family_cnt_day < 0
    OR loan_disb_cnt_day    != FLOOR(loan_disb_cnt_day)
    OR loan_repay_cnt_day   != FLOOR(loan_repay_cnt_day)
    OR wallet_txn_cnt_day   != FLOOR(wallet_txn_cnt_day)
;

-- ---------------------------------------------------------------------------
-- QA-VW2-04: Binary flag columns outside {0, 1}
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt,
    had_disbursement_day,
    had_repayment_day,
    alt_credit_signal_flag_day
FROM vw2_credit_v1_subscriber_day
WHERE
    had_disbursement_day       NOT IN (0, 1)
    OR had_repayment_day           NOT IN (0, 1)
    OR alt_credit_signal_flag_day  NOT IN (0, 1)
;

-- ---------------------------------------------------------------------------
-- QA-VW2-05: Disbursement count > 0 but amount = 0 (or vice versa)
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt,
    loan_disb_cnt_day,
    loan_disb_amt_day
FROM vw2_credit_v1_subscriber_day
WHERE
    (loan_disb_cnt_day > 0 AND loan_disb_amt_day = 0)
    OR (loan_disb_cnt_day = 0 AND loan_disb_amt_day > 0)
;

-- ---------------------------------------------------------------------------
-- QA-VW2-06: event_dt in the future
-- Daily rollup rows should never have a date beyond today.
-- ---------------------------------------------------------------------------
SELECT
    subscriber_msisdn,
    event_dt
FROM vw2_credit_v1_subscriber_day
WHERE event_dt > CURRENT_DATE
;
