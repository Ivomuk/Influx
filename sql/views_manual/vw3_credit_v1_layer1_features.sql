-- Manual INSERT script for vw3_credit_v1_layer1_features.
-- Depends on vw2_credit_v1_subscriber_day — run vw2 first.
-- Replace YYYY-MM-DD with the execution date before running.
--
-- Dimension 2 + Dimension 5: rolling windows (7/14/30/60/90-day), trend ratio
-- features, anti-gaming detection flags, and identity maturity signals.

SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE';

INSERT INTO hive.credit_engine.vw3_credit_v1_layer1_features
SELECT t.*, 'YYYY-MM-DD' AS run_date
FROM (
    -- Cast event_dt to DATE once here; Presto/Hive may return DATE columns as
    -- BIGINT (days since epoch) when reading from a Hive table.
    WITH vw2_data AS (
        SELECT
            CAST(event_dt AS DATE)  AS event_dt,
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

    anchor_days AS (
        SELECT DISTINCT
            event_dt AS feature_dt,
            subscriber_msisdn
        FROM vw2_data
    ),

    -- -----------------------------------------------------------------------
    -- 90-day window (longest lookback first to allow all shorter joins below)
    -- -----------------------------------------------------------------------
    window_90d AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(b.loan_disb_amt_day)      AS loan_disb_amt_90d,
            SUM(b.loan_repaid_amt_day)    AS loan_repaid_amt_90d,
            SUM(b.wallet_inflow_amt_day)  AS wallet_inflow_amt_90d,
            SUM(b.wallet_outflow_amt_day) AS wallet_outflow_amt_90d,
            SUM(b.spend_amt_day)          AS spend_amt_90d,
            SUM(b.loan_disb_cnt_day)      AS disbursement_cnt_90d,
            SUM(b.loan_repay_cnt_day)     AS repayment_cnt_90d,
            SUM(b.wallet_txn_cnt_day)     AS wallet_txn_cnt_90d,
            SUM(b.savings_amt_day)        AS savings_amt_90d,
            COUNT(DISTINCT CASE WHEN b.wallet_txn_cnt_day > 0 THEN b.event_dt END) AS wallet_active_days_90d,
            MIN(b.event_dt)               AS earliest_observed_dt
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -89, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- 60-day window
    -- -----------------------------------------------------------------------
    window_60d AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(b.loan_disb_amt_day)      AS loan_disb_amt_60d,
            SUM(b.loan_repaid_amt_day)    AS loan_repaid_amt_60d,
            SUM(b.wallet_inflow_amt_day)  AS wallet_inflow_amt_60d,
            SUM(b.spend_amt_day)          AS spend_amt_60d,
            SUM(b.savings_amt_day)        AS savings_amt_60d,
            SUM(b.loan_disb_cnt_day)      AS disbursement_cnt_60d,
            SUM(b.loan_repay_cnt_day)     AS repayment_cnt_60d,
            COUNT(DISTINCT CASE WHEN b.wallet_txn_cnt_day > 0 THEN b.event_dt END) AS wallet_active_days_60d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -59, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- 30-day window (existing core window)
    -- -----------------------------------------------------------------------
    window_30d AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(b.loan_disb_amt_day)      AS loan_disb_amt_30d,
            SUM(b.loan_repaid_amt_day)    AS loan_repaid_amt_30d,
            SUM(b.wallet_inflow_amt_day)  AS wallet_inflow_amt_30d,
            SUM(b.wallet_outflow_amt_day) AS wallet_outflow_amt_30d,
            SUM(b.spend_amt_day)          AS spend_amt_30d,
            SUM(b.savings_amt_day)        AS savings_amt_30d,
            SUM(b.loan_disb_cnt_day)      AS disbursement_cnt_30d,
            SUM(b.loan_repay_cnt_day)     AS repayment_cnt_30d,
            SUM(b.wallet_txn_cnt_day)     AS wallet_txn_cnt_30d,
            COUNT(DISTINCT CASE WHEN b.lender_family_cnt_day > 0 THEN b.event_dt END) AS active_lender_days_30d,
            MAX(b.lender_family_cnt_day)  AS active_lender_cnt_30d,
            MAX(b.alt_credit_signal_flag_day) AS alt_credit_active_flag_30d,
            MAX(CASE WHEN b.had_disbursement_day = 1 THEN b.event_dt END) AS last_disbursement_dt,
            MAX(CASE WHEN b.had_repayment_day    = 1 THEN b.event_dt END) AS last_repayment_dt,
            COUNT(DISTINCT CASE WHEN b.wallet_txn_cnt_day > 0 THEN b.event_dt END) AS wallet_active_days_30d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -29, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- 14-day window
    -- -----------------------------------------------------------------------
    window_14d AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(b.loan_disb_amt_day)      AS loan_disb_amt_14d,
            SUM(b.loan_repaid_amt_day)    AS loan_repaid_amt_14d,
            SUM(b.wallet_inflow_amt_day)  AS wallet_inflow_amt_14d,
            SUM(b.spend_amt_day)          AS spend_amt_14d,
            SUM(b.savings_amt_day)        AS savings_amt_14d,
            SUM(b.loan_disb_cnt_day)      AS disbursement_cnt_14d,
            SUM(b.loan_repay_cnt_day)     AS repayment_cnt_14d,
            COUNT(DISTINCT CASE WHEN b.wallet_txn_cnt_day > 0 THEN b.event_dt END) AS wallet_active_days_14d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -13, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- 7-day window
    -- -----------------------------------------------------------------------
    window_7d AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(b.loan_disb_amt_day)      AS loan_disb_amt_7d,
            SUM(b.loan_repaid_amt_day)    AS loan_repaid_amt_7d,
            SUM(b.wallet_inflow_amt_day)  AS wallet_inflow_amt_7d,
            SUM(b.spend_amt_day)          AS spend_amt_7d,
            SUM(b.savings_amt_day)        AS savings_amt_7d,
            SUM(b.loan_disb_cnt_day)      AS disbursement_cnt_7d,
            SUM(b.loan_repay_cnt_day)     AS repayment_cnt_7d,
            COUNT(DISTINCT CASE WHEN b.wallet_txn_cnt_day > 0 THEN b.event_dt END) AS wallet_active_days_7d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- Post-loan activity: last 7d vs prior 7d
    -- -----------------------------------------------------------------------
    post_loan_activity AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(CASE
                WHEN b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
                    THEN b.wallet_inflow_amt_day + b.wallet_outflow_amt_day + b.spend_amt_day
                ELSE 0.0
            END) AS wallet_activity_last_7d,
            SUM(CASE
                WHEN b.event_dt BETWEEN date_add('day', -13, a.feature_dt) AND date_add('day', -7, a.feature_dt)
                    THEN b.wallet_inflow_amt_day + b.wallet_outflow_amt_day + b.spend_amt_day
                ELSE 0.0
            END) AS wallet_activity_prev_7d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -13, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- Anti-gaming: inflow spike in last 2 days vs 30-day daily average
    -- -----------------------------------------------------------------------
    timing_manipulation AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            SUM(CASE
                WHEN b.event_dt >= date_add('day', -1, a.feature_dt)
                    THEN b.wallet_inflow_amt_day
                ELSE 0.0
            END) AS inflow_last_2d,
            SUM(b.wallet_inflow_amt_day) / NULLIF(COUNT(b.event_dt), 0) AS avg_daily_inflow_30d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -29, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- Anti-gaming: repayment ratio jump between 7-day windows
    -- -----------------------------------------------------------------------
    repayment_jump AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            CASE
                WHEN SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
                              THEN b.loan_disb_amt_day END) > 0
                THEN SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
                              THEN b.loan_repaid_amt_day END)
                     / SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
                                THEN b.loan_disb_amt_day END)
                ELSE NULL
            END AS repayment_ratio_last_7d,
            CASE
                WHEN SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -13, a.feature_dt)
                                                   AND date_add('day', -7, a.feature_dt)
                              THEN b.loan_disb_amt_day END) > 0
                THEN SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -13, a.feature_dt)
                                                   AND date_add('day', -7, a.feature_dt)
                              THEN b.loan_repaid_amt_day END)
                     / SUM(CASE WHEN b.event_dt BETWEEN date_add('day', -13, a.feature_dt)
                                                   AND date_add('day', -7, a.feature_dt)
                                THEN b.loan_disb_amt_day END)
                ELSE NULL
            END AS repayment_ratio_prev_7d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -13, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- Anti-gaming: loan cycling
    -- -----------------------------------------------------------------------
    loan_cycling AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            MAX(CASE
                WHEN b.had_disbursement_day = 1 AND b.had_repayment_day = 1
                    THEN 1
                ELSE 0
            END) AS same_day_disb_repay_flag,
            COUNT(CASE WHEN b.had_disbursement_day = 1 THEN 1 END) AS disb_days_7d
        FROM anchor_days a
        INNER JOIN vw2_data b
            ON  a.subscriber_msisdn = b.subscriber_msisdn
            AND b.event_dt BETWEEN date_add('day', -6, a.feature_dt) AND a.feature_dt
        GROUP BY a.feature_dt, a.subscriber_msisdn
    ),

    -- -----------------------------------------------------------------------
    -- Identity maturity
    -- -----------------------------------------------------------------------
    identity_maturity AS (
        SELECT
            a.feature_dt,
            a.subscriber_msisdn,
            date_diff('day', w90.earliest_observed_dt, a.feature_dt) AS sim_age_days,
            CASE
                WHEN w30.wallet_inflow_amt_30d > 0 AND w30.spend_amt_30d > 0 THEN 1
                ELSE 0
            END AS has_inflow_and_spend_flag,
            CASE
                WHEN w90.wallet_active_days_90d >= 30 THEN 'mature'
                WHEN w90.wallet_active_days_90d >= 10 THEN 'developing'
                ELSE 'thin'
            END AS sim_age_cohort
        FROM anchor_days a
        INNER JOIN window_90d w90
            ON a.feature_dt = w90.feature_dt AND a.subscriber_msisdn = w90.subscriber_msisdn
        INNER JOIN window_30d w30
            ON a.feature_dt = w30.feature_dt AND a.subscriber_msisdn = w30.subscriber_msisdn
    ),

    combined AS (
        SELECT
            w30.feature_dt,
            w30.subscriber_msisdn,
            w30.loan_disb_amt_30d,
            w30.loan_repaid_amt_30d,
            w30.wallet_inflow_amt_30d,
            w30.wallet_outflow_amt_30d,
            w30.spend_amt_30d,
            w30.savings_amt_30d,
            w30.disbursement_cnt_30d,
            w30.repayment_cnt_30d,
            w30.wallet_txn_cnt_30d,
            w30.active_lender_cnt_30d,
            w30.active_lender_days_30d,
            w30.alt_credit_active_flag_30d,
            w30.last_disbursement_dt,
            w30.last_repayment_dt,
            w30.wallet_active_days_30d,
            w7.loan_disb_amt_7d,
            w7.loan_repaid_amt_7d,
            w7.wallet_inflow_amt_7d,
            w7.spend_amt_7d,
            w7.savings_amt_7d,
            w7.disbursement_cnt_7d,
            w7.repayment_cnt_7d,
            w7.wallet_active_days_7d,
            w14.loan_disb_amt_14d,
            w14.loan_repaid_amt_14d,
            w14.wallet_inflow_amt_14d,
            w14.spend_amt_14d,
            w14.savings_amt_14d,
            w14.disbursement_cnt_14d,
            w14.repayment_cnt_14d,
            w14.wallet_active_days_14d,
            w60.loan_disb_amt_60d,
            w60.loan_repaid_amt_60d,
            w60.wallet_inflow_amt_60d,
            w60.spend_amt_60d,
            w60.savings_amt_60d,
            w60.disbursement_cnt_60d,
            w60.repayment_cnt_60d,
            w60.wallet_active_days_60d,
            w90.loan_disb_amt_90d,
            w90.loan_repaid_amt_90d,
            w90.wallet_inflow_amt_90d,
            w90.wallet_outflow_amt_90d,
            w90.spend_amt_90d,
            w90.savings_amt_90d,
            w90.disbursement_cnt_90d,
            w90.repayment_cnt_90d,
            w90.wallet_txn_cnt_90d,
            w90.wallet_active_days_90d,
            pla.wallet_activity_last_7d,
            pla.wallet_activity_prev_7d,
            tm.inflow_last_2d,
            tm.avg_daily_inflow_30d,
            rj.repayment_ratio_last_7d,
            rj.repayment_ratio_prev_7d,
            lc.same_day_disb_repay_flag,
            lc.disb_days_7d,
            im.sim_age_days,
            im.has_inflow_and_spend_flag,
            im.sim_age_cohort
        FROM window_30d w30
        LEFT JOIN window_7d  w7  ON w30.feature_dt = w7.feature_dt  AND w30.subscriber_msisdn = w7.subscriber_msisdn
        LEFT JOIN window_14d w14 ON w30.feature_dt = w14.feature_dt AND w30.subscriber_msisdn = w14.subscriber_msisdn
        LEFT JOIN window_60d w60 ON w30.feature_dt = w60.feature_dt AND w30.subscriber_msisdn = w60.subscriber_msisdn
        LEFT JOIN window_90d w90 ON w30.feature_dt = w90.feature_dt AND w30.subscriber_msisdn = w90.subscriber_msisdn
        LEFT JOIN post_loan_activity pla ON w30.feature_dt = pla.feature_dt AND w30.subscriber_msisdn = pla.subscriber_msisdn
        LEFT JOIN timing_manipulation tm  ON w30.feature_dt = tm.feature_dt  AND w30.subscriber_msisdn = tm.subscriber_msisdn
        LEFT JOIN repayment_jump      rj  ON w30.feature_dt = rj.feature_dt  AND w30.subscriber_msisdn = rj.subscriber_msisdn
        LEFT JOIN loan_cycling        lc  ON w30.feature_dt = lc.feature_dt  AND w30.subscriber_msisdn = lc.subscriber_msisdn
        LEFT JOIN identity_maturity   im  ON w30.feature_dt = im.feature_dt  AND w30.subscriber_msisdn = im.subscriber_msisdn
    ),

    final_features AS (
        SELECT
            feature_dt,
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
                WHEN repayment_ratio_last_7d IS NOT NULL AND repayment_ratio_prev_7d IS NOT NULL
                    THEN repayment_ratio_last_7d - repayment_ratio_prev_7d
                ELSE NULL
            END AS repayment_ratio_trend_7d,
            CASE WHEN last_disbursement_dt IS NOT NULL THEN date_diff('day', last_disbursement_dt, feature_dt) ELSE NULL END AS days_since_last_disbursement,
            CASE WHEN last_repayment_dt    IS NOT NULL THEN date_diff('day', last_repayment_dt,    feature_dt) ELSE NULL END AS days_since_last_repayment,
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
                WHEN repayment_ratio_last_7d IS NOT NULL
                     AND repayment_ratio_prev_7d IS NOT NULL
                     AND (repayment_ratio_last_7d - repayment_ratio_prev_7d) > 0.50
                    THEN 1
                ELSE 0
            END AS suspicious_repayment_jump_flag,
            COALESCE(same_day_disb_repay_flag, 0) AS loan_cycling_flag,
            COALESCE(sim_age_days, 0) AS sim_age_days,
            COALESCE(has_inflow_and_spend_flag, 0) AS has_inflow_and_spend_flag,
            COALESCE(sim_age_cohort, 'thin') AS sim_age_cohort,
            CASE
                WHEN sim_age_days >= 90 AND has_inflow_and_spend_flag = 1 THEN 1.00
                WHEN sim_age_days >= 30 AND has_inflow_and_spend_flag = 1 THEN 0.85
                WHEN sim_age_days >= 14                                    THEN 0.70
                ELSE 0.50
            END AS identity_confidence_score_v2
        FROM combined
    )

    SELECT
        feature_dt,
        subscriber_msisdn,
        outstanding_exposure_amt,
        active_lender_cnt_30d,
        disbursement_cnt_30d,
        disbursement_cnt_7d,
        disbursement_cnt_14d,
        disbursement_cnt_60d,
        disbursement_cnt_90d,
        days_since_last_disbursement,
        repayment_ratio_30d,
        repayment_ratio_14d,
        repayment_ratio_60d,
        repayment_ratio_90d,
        repayment_ratio_trend_7d,
        repayment_cnt_30d,
        repayment_cnt_7d,
        days_since_last_repayment,
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
        stacked_borrowing_flag_30d,
        repeated_borrowing_flag_30d,
        alt_credit_active_flag_30d,
        active_lender_days_30d,
        loan_disb_amt_30d,
        loan_repaid_amt_30d,
        last_disbursement_dt,
        last_repayment_dt,
        timing_manipulation_flag,
        suspicious_repayment_jump_flag,
        loan_cycling_flag,
        sim_age_days,
        has_inflow_and_spend_flag,
        sim_age_cohort,
        identity_confidence_score_v2
    FROM final_features
) t
WHERE t.feature_dt = DATE 'YYYY-MM-DD'
;

SET SESSION hive.insert_existing_partitions_behavior = 'APPEND';
