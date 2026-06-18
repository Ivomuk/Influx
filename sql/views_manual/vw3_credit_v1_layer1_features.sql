-- Manual INSERT script for vw3_credit_v1_layer1_features.
-- Depends on vw2_credit_v1_subscriber_day — run vw2 first.
--
-- Optimised for single feature_dt: replaces 9 self-join window CTEs with a
-- single-scan conditional aggregation over vw2. All window boundaries are
-- constant offsets from DATE '2026-05-31'.

SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw3_credit_v1_layer1_features
SELECT t.*, '2026-05-31' AS run_date
FROM (
    WITH vw2_data AS (
        SELECT
            CAST(event_dt AS DATE) AS event_dt,
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
        FROM vw2_credit_v1_subscriber_day
    ),

    subscribers AS (
        SELECT DISTINCT subscriber_msisdn
        FROM vw2_data
        WHERE event_dt = DATE '2026-05-31'
    ),

    aggregates AS (
        SELECT
            d.subscriber_msisdn,

            -- 7-day amounts (2026-05-25 → 2026-05-31)
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_disb_amt_day    ELSE 0 END) AS loan_disb_amt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_repaid_amt_day  ELSE 0 END) AS loan_repaid_amt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.wallet_inflow_amt_day ELSE 0 END) AS wallet_inflow_amt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.spend_amt_day         ELSE 0 END) AS spend_amt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.savings_amt_day       ELSE 0 END) AS savings_amt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_disb_cnt_day     ELSE 0 END) AS disbursement_cnt_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_repay_cnt_day    ELSE 0 END) AS repayment_cnt_7d,
            COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-05-25' AND d.wallet_txn_cnt_day > 0 THEN d.event_dt END) AS wallet_active_days_7d,

            -- 14-day amounts (2026-05-18 → 2026-05-31)
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.loan_disb_amt_day    ELSE 0 END) AS loan_disb_amt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.loan_repaid_amt_day  ELSE 0 END) AS loan_repaid_amt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.wallet_inflow_amt_day ELSE 0 END) AS wallet_inflow_amt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.spend_amt_day         ELSE 0 END) AS spend_amt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.savings_amt_day       ELSE 0 END) AS savings_amt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.loan_disb_cnt_day     ELSE 0 END) AS disbursement_cnt_14d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-18' THEN d.loan_repay_cnt_day    ELSE 0 END) AS repayment_cnt_14d,
            COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-05-18' AND d.wallet_txn_cnt_day > 0 THEN d.event_dt END) AS wallet_active_days_14d,

            -- 30-day amounts (2026-05-02 → 2026-05-31)
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.loan_disb_amt_day    ELSE 0 END) AS loan_disb_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.loan_repaid_amt_day  ELSE 0 END) AS loan_repaid_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.wallet_inflow_amt_day ELSE 0 END) AS wallet_inflow_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.wallet_outflow_amt_day ELSE 0 END) AS wallet_outflow_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.spend_amt_day         ELSE 0 END) AS spend_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.savings_amt_day       ELSE 0 END) AS savings_amt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.loan_disb_cnt_day     ELSE 0 END) AS disbursement_cnt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.loan_repay_cnt_day    ELSE 0 END) AS repayment_cnt_30d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.wallet_txn_cnt_day    ELSE 0 END) AS wallet_txn_cnt_30d,
            COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-05-02' AND d.wallet_txn_cnt_day > 0 THEN d.event_dt END) AS wallet_active_days_30d,
            MAX(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.lender_family_cnt_day ELSE 0 END) AS active_lender_cnt_30d,
            COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-05-02' AND d.lender_family_cnt_day > 0 THEN d.event_dt END) AS active_lender_days_30d,
            MAX(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.alt_credit_signal_flag_day ELSE 0 END) AS alt_credit_active_flag_30d,
            CAST(MAX(CASE WHEN d.event_dt >= DATE '2026-05-02' AND d.had_disbursement_day = 1 THEN CAST(d.event_dt AS DATE) END) AS DATE) AS last_disbursement_dt,
            CAST(MAX(CASE WHEN d.event_dt >= DATE '2026-05-02' AND d.had_repayment_day = 1    THEN CAST(d.event_dt AS DATE) END) AS DATE) AS last_repayment_dt,

            -- 60-day amounts (2026-04-02 → 2026-05-31)
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.loan_disb_amt_day    ELSE 0 END) AS loan_disb_amt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.loan_repaid_amt_day  ELSE 0 END) AS loan_repaid_amt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.wallet_inflow_amt_day ELSE 0 END) AS wallet_inflow_amt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.spend_amt_day         ELSE 0 END) AS spend_amt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.savings_amt_day       ELSE 0 END) AS savings_amt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.loan_disb_cnt_day     ELSE 0 END) AS disbursement_cnt_60d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-04-02' THEN d.loan_repay_cnt_day    ELSE 0 END) AS repayment_cnt_60d,
            COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-04-02' AND d.wallet_txn_cnt_day > 0 THEN d.event_dt END) AS wallet_active_days_60d,

            -- 90-day amounts (2026-03-03 → 2026-05-31)
            SUM(d.loan_disb_amt_day)      AS loan_disb_amt_90d,
            SUM(d.loan_repaid_amt_day)    AS loan_repaid_amt_90d,
            SUM(d.wallet_inflow_amt_day)  AS wallet_inflow_amt_90d,
            SUM(d.wallet_outflow_amt_day) AS wallet_outflow_amt_90d,
            SUM(d.spend_amt_day)          AS spend_amt_90d,
            SUM(d.savings_amt_day)        AS savings_amt_90d,
            SUM(d.loan_disb_cnt_day)      AS disbursement_cnt_90d,
            SUM(d.loan_repay_cnt_day)     AS repayment_cnt_90d,
            SUM(d.wallet_txn_cnt_day)     AS wallet_txn_cnt_90d,
            COUNT(DISTINCT CASE WHEN d.wallet_txn_cnt_day > 0 THEN d.event_dt END) AS wallet_active_days_90d,
            CAST(MIN(d.event_dt) AS DATE) AS earliest_observed_dt,

            -- Post-loan activity: last 7d vs prev 7d
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25'
                     THEN d.wallet_inflow_amt_day + d.wallet_outflow_amt_day + d.spend_amt_day ELSE 0 END) AS wallet_activity_last_7d,
            SUM(CASE WHEN d.event_dt BETWEEN DATE '2026-05-18' AND DATE '2026-05-24'
                     THEN d.wallet_inflow_amt_day + d.wallet_outflow_amt_day + d.spend_amt_day ELSE 0 END) AS wallet_activity_prev_7d,

            -- Timing manipulation: inflow spike last 2d vs 30d avg
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-30' THEN d.wallet_inflow_amt_day ELSE 0 END) AS inflow_last_2d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.wallet_inflow_amt_day ELSE 0 END)
                / NULLIF(COUNT(DISTINCT CASE WHEN d.event_dt >= DATE '2026-05-02' THEN d.event_dt END), 0) AS avg_daily_inflow_30d,

            -- Repayment jump: last 7d vs prev 7d ratios
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_repaid_amt_day ELSE 0 END) AS repaid_last_7d,
            SUM(CASE WHEN d.event_dt >= DATE '2026-05-25' THEN d.loan_disb_amt_day   ELSE 0 END) AS disb_last_7d,
            SUM(CASE WHEN d.event_dt BETWEEN DATE '2026-05-18' AND DATE '2026-05-24' THEN d.loan_repaid_amt_day ELSE 0 END) AS repaid_prev_7d,
            SUM(CASE WHEN d.event_dt BETWEEN DATE '2026-05-18' AND DATE '2026-05-24' THEN d.loan_disb_amt_day   ELSE 0 END) AS disb_prev_7d,

            -- Loan cycling: same-day disbursement+repayment in last 7d
            MAX(CASE WHEN d.event_dt >= DATE '2026-05-25' AND d.had_disbursement_day = 1 AND d.had_repayment_day = 1 THEN 1 ELSE 0 END) AS same_day_disb_repay_flag,
            COUNT(CASE WHEN d.event_dt >= DATE '2026-05-25' AND d.had_disbursement_day = 1 THEN 1 END) AS disb_days_7d

        FROM vw2_data d
        INNER JOIN subscribers s ON d.subscriber_msisdn = s.subscriber_msisdn
        WHERE d.event_dt BETWEEN DATE '2026-03-03' AND DATE '2026-05-31'
        GROUP BY d.subscriber_msisdn
    ),

    final_features AS (
        SELECT
            DATE '2026-05-31' AS feature_dt,
            subscriber_msisdn,

            (loan_disb_amt_30d - loan_repaid_amt_30d) AS outstanding_exposure_amt,
            loan_disb_amt_30d,
            loan_repaid_amt_30d,
            last_disbursement_dt,
            last_repayment_dt,

            CASE WHEN loan_disb_amt_30d > 0 THEN loan_repaid_amt_30d / loan_disb_amt_30d ELSE NULL END AS repayment_ratio_30d,
            CASE WHEN loan_disb_amt_14d > 0 THEN loan_repaid_amt_14d / loan_disb_amt_14d ELSE NULL END AS repayment_ratio_14d,
            CASE WHEN loan_disb_amt_60d > 0 THEN loan_repaid_amt_60d / loan_disb_amt_60d ELSE NULL END AS repayment_ratio_60d,
            CASE WHEN loan_disb_amt_90d > 0 THEN loan_repaid_amt_90d / loan_disb_amt_90d ELSE NULL END AS repayment_ratio_90d,

            CASE
                WHEN disb_last_7d > 0 AND disb_prev_7d > 0
                    THEN (repaid_last_7d / disb_last_7d) - (repaid_prev_7d / disb_prev_7d)
                ELSE NULL
            END AS repayment_ratio_trend_7d,

            CASE WHEN last_disbursement_dt IS NOT NULL THEN date_diff('day', last_disbursement_dt, DATE '2026-05-31') ELSE NULL END AS days_since_last_disbursement,
            CASE WHEN last_repayment_dt    IS NOT NULL THEN date_diff('day', last_repayment_dt,    DATE '2026-05-31') ELSE NULL END AS days_since_last_repayment,

            wallet_inflow_amt_30d,
            wallet_outflow_amt_30d,
            spend_amt_30d,
            savings_amt_30d,
            wallet_txn_cnt_30d,
            wallet_active_days_30d,
            wallet_inflow_amt_7d,
            wallet_inflow_amt_14d,
            wallet_inflow_amt_60d,
            wallet_inflow_amt_90d,
            wallet_active_days_7d,
            wallet_active_days_14d,
            wallet_active_days_60d,
            wallet_active_days_90d,

            CASE
                WHEN wallet_active_days_90d > 0 AND wallet_inflow_amt_90d > 0
                    THEN (wallet_inflow_amt_7d / NULLIF(wallet_active_days_7d, 0))
                         / (wallet_inflow_amt_90d / wallet_active_days_90d)
                ELSE NULL
            END AS wallet_inflow_trend_7d_vs_90d,

            wallet_activity_last_7d,
            wallet_activity_prev_7d,
            CASE
                WHEN wallet_activity_prev_7d > 0
                    THEN (wallet_activity_last_7d - wallet_activity_prev_7d) / wallet_activity_prev_7d
                ELSE NULL
            END AS post_loan_wallet_activity_change_ratio,

            disbursement_cnt_30d,
            disbursement_cnt_7d,
            disbursement_cnt_14d,
            disbursement_cnt_60d,
            disbursement_cnt_90d,
            repayment_cnt_30d,
            repayment_cnt_7d,

            active_lender_cnt_30d,
            active_lender_days_30d,
            alt_credit_active_flag_30d,

            CASE WHEN active_lender_cnt_30d >= 2 THEN 1 ELSE 0 END AS stacked_borrowing_flag_30d,
            CASE WHEN disbursement_cnt_30d  >= 3 THEN 1 ELSE 0 END AS repeated_borrowing_flag_30d,

            CASE
                WHEN avg_daily_inflow_30d > 0
                     AND inflow_last_2d / avg_daily_inflow_30d > 2.0
                    THEN 1
                ELSE 0
            END AS timing_manipulation_flag,

            CASE
                WHEN disb_last_7d > 0 AND disb_prev_7d > 0
                     AND ((repaid_last_7d / disb_last_7d) - (repaid_prev_7d / disb_prev_7d)) > 0.50
                    THEN 1
                ELSE 0
            END AS suspicious_repayment_jump_flag,

            COALESCE(same_day_disb_repay_flag, 0) AS loan_cycling_flag,

            COALESCE(date_diff('day', earliest_observed_dt, DATE '2026-05-31'), 0) AS sim_age_days,

            CASE
                WHEN wallet_inflow_amt_30d > 0 AND spend_amt_30d > 0 THEN 1
                ELSE 0
            END AS has_inflow_and_spend_flag,

            CASE
                WHEN wallet_active_days_90d >= 30 THEN 'mature'
                WHEN wallet_active_days_90d >= 10 THEN 'developing'
                ELSE 'thin'
            END AS sim_age_cohort,

            CASE
                WHEN COALESCE(date_diff('day', earliest_observed_dt, DATE '2026-05-31'), 0) >= 90
                     AND wallet_inflow_amt_30d > 0 AND spend_amt_30d > 0 THEN 1.00
                WHEN COALESCE(date_diff('day', earliest_observed_dt, DATE '2026-05-31'), 0) >= 30
                     AND wallet_inflow_amt_30d > 0 AND spend_amt_30d > 0 THEN 0.85
                WHEN COALESCE(date_diff('day', earliest_observed_dt, DATE '2026-05-31'), 0) >= 14
                    THEN 0.70
                ELSE 0.50
            END AS identity_confidence_score_v2

        FROM aggregates
    )

    SELECT
        feature_dt,
        subscriber_msisdn,
        outstanding_exposure_amt,
        loan_disb_amt_30d,
        loan_repaid_amt_30d,
        last_disbursement_dt,
        last_repayment_dt,
        repayment_ratio_30d,
        repayment_ratio_14d,
        repayment_ratio_60d,
        repayment_ratio_90d,
        repayment_ratio_trend_7d,
        days_since_last_disbursement,
        wallet_inflow_amt_30d,
        wallet_outflow_amt_30d,
        spend_amt_30d,
        savings_amt_30d,
        wallet_txn_cnt_30d,
        wallet_active_days_30d,
        wallet_inflow_amt_7d,
        wallet_inflow_amt_14d,
        wallet_inflow_amt_60d,
        wallet_inflow_amt_90d,
        wallet_active_days_7d,
        wallet_active_days_14d,
        wallet_active_days_60d,
        wallet_active_days_90d,
        wallet_inflow_trend_7d_vs_90d,
        wallet_activity_last_7d,
        wallet_activity_prev_7d,
        post_loan_wallet_activity_change_ratio,
        disbursement_cnt_30d,
        disbursement_cnt_7d,
        disbursement_cnt_14d,
        disbursement_cnt_60d,
        disbursement_cnt_90d,
        repayment_cnt_30d,
        repayment_cnt_7d,
        days_since_last_repayment,
        active_lender_cnt_30d,
        active_lender_days_30d,
        alt_credit_active_flag_30d,
        stacked_borrowing_flag_30d,
        repeated_borrowing_flag_30d,
        timing_manipulation_flag,
        suspicious_repayment_jump_flag,
        loan_cycling_flag,
        sim_age_days,
        has_inflow_and_spend_flag,
        sim_age_cohort,
        identity_confidence_score_v2
    FROM final_features
) t
WHERE t.feature_dt = DATE '2026-05-31'
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';
