# Dimension 6 — Outcome Tracking, Calibration, Fairness Monitoring, and Explainability.
# Stores decision snapshots with all input features so actual outcomes can be joined later.
# Calibration results feed back into model/rule recalibration for Dimension 1.

import pandas as pd
import numpy as np
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Decision Snapshot Storage
# ---------------------------------------------------------------------------

def store_decision_snapshot(final_capacity_df, layer0_df, layer1_df, config):
    """
    Creates a timestamped snapshot joining the final decision to all input features.
    This record is the ground truth for future calibration and model training.
    Stored columns: decision outputs + all Layer 0 scores + key Layer 1 features.
    """
    _snapshot_cols = [
        'feature_dt', 'subscriber_msisdn', 'selected_action', 'CreditLimit',
        'expected_tnv', 'primary_policy_reason', 'update_direction',
        'validity_days', 'output_version',
        'binding_cap', 'all_triggered_reasons', 'config_hash',
        'policy_version', 'decision_status', 'after_vw6_cap',
    ]
    _available_snapshot_cols = [c for c in _snapshot_cols if c in final_capacity_df.columns]
    snapshot_df = (
        final_capacity_df[_available_snapshot_cols]
        .merge(
            layer0_df[[
                'feature_dt', 'subscriber_msisdn',
                'expected_repayment_probability_v1_rule',
                'expected_credit_loss_rate_v1_rule',
                'churn_cooling_probability_v1_rule',
                'expected_future_transaction_margin_score_v1_rule',
                'debt_stress_index_v1',
                'fraud_abuse_risk_score_v1_rule',
                'behavior_consistency_score_v1_rule',
                'identity_confidence_score_v1_rule',
                'customer_lifetime_value_contribution_v1_rule'
            ]],
            on=['feature_dt', 'subscriber_msisdn'],
            how='left'
        )
        .merge(
            layer1_df[[
                'feature_dt', 'subscriber_msisdn',
                'outstanding_exposure_amt',
                'repayment_ratio_30d',
                'wallet_inflow_amt_30d',
                'wallet_active_days_30d',
                'active_lender_cnt_30d',
                'disbursement_cnt_30d',
                'stacked_borrowing_flag_30d',
                'repeated_borrowing_flag_30d',
                'days_since_last_disbursement',
                'days_since_last_repayment',
                'post_loan_wallet_activity_change_ratio'
            ]],
            on=['feature_dt', 'subscriber_msisdn'],
            how='left'
        )
    )

    snapshot_df['snapshot_created_at'] = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot_df['snapshot_version'] = config['output_version']

    return snapshot_df.copy()


# ---------------------------------------------------------------------------
# Realised Outcome Computation
# ---------------------------------------------------------------------------

def compute_realised_outcomes(snapshot_df, lender_feedback_df, evaluation_date, config):
    """
    Joins decision snapshots to actual repayment and default outcomes.
    Only evaluates snapshots old enough for outcomes to have materialised
    (snapshot_date + outcome_lookback_days <= evaluation_date).

    Returns one row per evaluable snapshot with:
      did_repay_flag          : 1 if any repayment was observed in the outcome window
      did_default_flag        : 1 if a DEFAULT event was reported in the outcome window
      actual_repayment_ratio  : total repaid / CreditLimit
      days_to_first_repayment : days from snapshot date to first repayment event
    """
    lookback_days = config['outcome_lookback_days']
    eval_date = pd.to_datetime(evaluation_date)

    snap_df = snapshot_df.copy()
    snap_df['feature_dt'] = pd.to_datetime(snap_df['feature_dt'])
    evaluable_df = snap_df[
        snap_df['feature_dt'] <= eval_date - pd.Timedelta(days=lookback_days)
    ].copy()

    if evaluable_df.empty:
        return pd.DataFrame()

    repayments = lender_feedback_df[lender_feedback_df['is_repayment'] == 1][
        ['msisdn', 'event_date', 'event_amount']
    ].copy()
    repayments['event_date'] = pd.to_datetime(repayments['event_date'])

    defaults = lender_feedback_df[lender_feedback_df['is_default'] == 1][
        ['msisdn', 'event_date']
    ].copy()
    defaults['event_date'] = pd.to_datetime(defaults['event_date'])

    outcome_rows = []
    for _, snap_row in evaluable_df.iterrows():
        msisdn = snap_row['subscriber_msisdn']
        snap_dt = snap_row['feature_dt']
        window_end = snap_dt + pd.Timedelta(days=lookback_days)

        sub_repayments = repayments[
            (repayments['msisdn'] == msisdn)
            & (repayments['event_date'] >= snap_dt)
            & (repayments['event_date'] <= window_end)
        ]
        sub_defaults = defaults[
            (defaults['msisdn'] == msisdn)
            & (defaults['event_date'] >= snap_dt)
            & (defaults['event_date'] <= window_end)
        ]

        total_repaid = sub_repayments['event_amount'].sum()
        did_repay = int(len(sub_repayments) > 0)
        did_default = int(len(sub_defaults) > 0)

        credit_limit = snap_row.get('CreditLimit', 0) or 0
        actual_repayment_ratio = (
            total_repaid / credit_limit if credit_limit > 0 else None
        )

        days_to_first = None
        if did_repay:
            days_to_first = int((sub_repayments['event_date'].min() - snap_dt).days)

        outcome_rows.append({
            'feature_dt': snap_dt,
            'subscriber_msisdn': msisdn,
            'predicted_repayment_probability': snap_row.get('expected_repayment_probability_v1_rule'),
            'predicted_credit_loss_rate': snap_row.get('expected_credit_loss_rate_v1_rule'),
            'predicted_churn_probability': snap_row.get('churn_cooling_probability_v1_rule'),
            'debt_stress_index_v1': snap_row.get('debt_stress_index_v1'),
            'selected_action': snap_row.get('selected_action'),
            'CreditLimit': credit_limit,
            'did_repay_flag': did_repay,
            'did_default_flag': did_default,
            'actual_repayment_ratio': actual_repayment_ratio,
            'days_to_first_repayment': days_to_first,
            'evaluation_date': eval_date,
            'outcome_window_days': lookback_days
        })

    return pd.DataFrame(outcome_rows)


# ---------------------------------------------------------------------------
# Calibration Analysis
# ---------------------------------------------------------------------------

def compute_calibration_metrics(realised_outcomes_df, config):
    """
    Computes repayment probability calibration by bucketing predictions into
    equal-frequency deciles and comparing predicted vs actual repayment rate.
    Flags buckets where |predicted - actual| exceeds the miscalibration threshold.
    A well-calibrated model/rule set shows calibration_error < 0.10 in all buckets.
    """
    calib_df = realised_outcomes_df.dropna(
        subset=['predicted_repayment_probability', 'did_repay_flag']
    ).copy()

    if calib_df.empty:
        return pd.DataFrame()

    bucket_count = config['calibration_bucket_count']
    min_bucket_size = config['calibration_min_bucket_size']
    error_threshold = config['calibration_miscalibration_threshold']

    calib_df['prob_bucket'] = pd.qcut(
        calib_df['predicted_repayment_probability'],
        q=bucket_count,
        duplicates='drop',
        labels=False
    )

    summary = (
        calib_df.groupby('prob_bucket')
        .agg(
            subscriber_count=('did_repay_flag', 'count'),
            avg_predicted_probability=('predicted_repayment_probability', 'mean'),
            actual_repayment_rate=('did_repay_flag', 'mean')
        )
        .reset_index()
    )
    summary = summary[summary['subscriber_count'] >= min_bucket_size].copy()

    summary['calibration_error'] = (
        summary['avg_predicted_probability'] - summary['actual_repayment_rate']
    ).abs()
    summary['is_miscalibrated_flag'] = (
        summary['calibration_error'] > error_threshold
    ).astype(int)

    overall_error = summary['calibration_error'].mean()
    summary['overall_calibration_score'] = round(1.0 - overall_error, 4)

    return summary


# ---------------------------------------------------------------------------
# DSI Accuracy Validation
# ---------------------------------------------------------------------------

def compute_dsi_accuracy(realised_outcomes_df, config):
    """
    Validates that higher DSI bands produce higher actual default rates (monotonicity check).
    A non-monotonic result means the DSI formula weights need recalibration.
    Returns one row per DSI band with actual_default_rate and monotonicity flag.
    """
    dsi_df = realised_outcomes_df.dropna(
        subset=['debt_stress_index_v1', 'did_default_flag']
    ).copy()

    if dsi_df.empty:
        return pd.DataFrame()

    bands = config['dsi_bands']
    labels = config['dsi_band_labels']
    min_size = config['dsi_min_band_size']

    dsi_df['dsi_band'] = pd.cut(
        dsi_df['debt_stress_index_v1'],
        bins=bands,
        labels=labels,
        include_lowest=True,
        right=False
    )

    accuracy_df = (
        dsi_df.groupby('dsi_band', observed=True)
        .agg(
            subscriber_count=('did_default_flag', 'count'),
            actual_default_rate=('did_default_flag', 'mean')
        )
        .reset_index()
    )
    accuracy_df = accuracy_df[accuracy_df['subscriber_count'] >= min_size].copy()

    rates = accuracy_df['actual_default_rate'].values
    is_monotonic = all(rates[i] <= rates[i + 1] for i in range(len(rates) - 1))
    accuracy_df['dsi_is_monotonic'] = int(is_monotonic)

    if not is_monotonic:
        print('WARNING: DSI default rates are not monotonically increasing — DSI weights need recalibration.')

    return accuracy_df


# ---------------------------------------------------------------------------
# Fairness Monitoring
# ---------------------------------------------------------------------------

def compute_fairness_metrics(decision_df, segment_cols, config):
    """
    Computes restrict rate and average CreditLimit per subscriber segment.
    Flags segments where restrict rate exceeds disparate_impact_restrict_rate_multiplier
    times the system-wide rate — a proxy for disparate impact.
    Only segments present in decision_df and with >= min_segment_size subscribers are evaluated.
    Returns empty DataFrame if no valid segment_cols are found.
    """
    min_size = config['min_segment_size']
    multiplier = config['disparate_impact_restrict_rate_multiplier']

    df = decision_df.copy()
    df['is_restricted_flag'] = df['selected_action'].isin(['DECLINE', 'RESTRICT']).astype(int)

    system_restrict_rate = df['is_restricted_flag'].mean()
    system_avg_credit_limit = df['CreditLimit'].mean()

    fairness_rows = []
    for seg_col in segment_cols:
        if seg_col not in df.columns:
            continue

        seg_summary = (
            df.groupby(seg_col)
            .agg(
                subscriber_count=('is_restricted_flag', 'count'),
                segment_restrict_rate=('is_restricted_flag', 'mean'),
                segment_avg_credit_limit=('CreditLimit', 'mean')
            )
            .reset_index()
            .rename(columns={seg_col: 'segment_value'})
        )
        seg_summary = seg_summary[seg_summary['subscriber_count'] >= min_size].copy()
        seg_summary['segment_col'] = seg_col
        seg_summary['system_restrict_rate'] = system_restrict_rate
        seg_summary['system_avg_credit_limit'] = system_avg_credit_limit
        seg_summary['restrict_rate_ratio'] = (
            seg_summary['segment_restrict_rate']
            / max(system_restrict_rate, 1e-6)
        )
        seg_summary['disparate_impact_flag'] = (
            seg_summary['restrict_rate_ratio'] > multiplier
        ).astype(int)

        fairness_rows.append(seg_summary)

    if not fairness_rows:
        return pd.DataFrame()

    return pd.concat(fairness_rows, ignore_index=True)[[
        'segment_col', 'segment_value', 'subscriber_count',
        'segment_restrict_rate', 'system_restrict_rate', 'restrict_rate_ratio',
        'disparate_impact_flag', 'segment_avg_credit_limit', 'system_avg_credit_limit'
    ]].copy()


# ---------------------------------------------------------------------------
# Explainability
# ---------------------------------------------------------------------------

def generate_decision_explanation(subscriber_row, explainability_config):
    """
    Produces a human-readable decision explanation for a single subscriber.
    subscriber_row: dict or pandas Series with decision and feature fields.
    Returns a dict containing:
      primary_driver      : plain-language reason
      counterfactual_text : what the subscriber can do to improve
      full_text           : complete explanation paragraph
      sms_text            : truncated to sms_max_chars for SMS/USSD delivery
    """
    reason = str(subscriber_row.get('primary_policy_reason', 'PASS'))
    action = str(subscriber_row.get('selected_action', 'UNKNOWN'))
    credit_limit = subscriber_row.get('CreditLimit', 0) or 0
    sms_max = explainability_config.get('sms_max_chars', 160)

    reason_descriptions = explainability_config['reason_descriptions']
    counterfactuals = explainability_config['counterfactual_texts']

    primary_driver = reason_descriptions.get(reason, reason.lower().replace('_', ' '))
    counterfactual = counterfactuals.get(reason, 'Continue regular wallet activity and repayments.')

    if action in ('DECLINE', 'RESTRICT'):
        outcome_text = 'no lending capacity is available at this time'
    else:
        outcome_text = f'your approved lending capacity is {int(credit_limit):,}'

    full_text = (
        f'Decision: {action}. {outcome_text.capitalize()}. '
        f'Primary reason: {primary_driver}. '
        f'{counterfactual}'
    )

    return {
        'subscriber_msisdn': subscriber_row.get('subscriber_msisdn'),
        'selected_action': action,
        'CreditLimit': credit_limit,
        'primary_reason_code': reason,
        'primary_driver': primary_driver,
        'counterfactual_text': counterfactual,
        'full_text': full_text,
        'sms_text': full_text[:sms_max]
    }


def generate_all_explanations(final_capacity_df, explainability_config):
    """Applies generate_decision_explanation to every row in final_capacity_df."""
    return pd.DataFrame([
        generate_decision_explanation(row, explainability_config)
        for _, row in final_capacity_df.iterrows()
    ])


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run_dim6_outcome_tracking(
    final_capacity_df, layer0_df, layer1_df,
    lender_feedback_df, evaluation_date,
    segment_cols=None,
    outcome_config=None, fairness_config=None, explainability_config=None
):
    """
    Orchestrates the full Dimension 6 outcome tracking pipeline:
      1. Store decision snapshot
      2. Compute realised outcomes (if enough time has elapsed)
      3. Compute calibration metrics
      4. Compute DSI accuracy
      5. Compute fairness metrics
      6. Generate decision explanations for all subscribers

    Returns a dict with all DataFrames keyed by name.
    Calibration and DSI outputs are empty DataFrames if not enough outcomes exist yet.
    """
    local_outcome_config = OUTCOME_TRACKING_CONFIG.copy() if outcome_config is None else outcome_config.copy()
    local_fairness_config = FAIRNESS_CONFIG.copy() if fairness_config is None else fairness_config.copy()
    local_explainability_config = EXPLAINABILITY_CONFIG.copy() if explainability_config is None else explainability_config.copy()

    if segment_cols is not None:
        local_fairness_config['monitored_segment_cols'] = segment_cols

    snapshot_df = store_decision_snapshot(
        final_capacity_df, layer0_df, layer1_df, local_outcome_config
    )

    realised_outcomes_df = compute_realised_outcomes(
        snapshot_df, lender_feedback_df, evaluation_date, local_outcome_config
    )

    calibration_df = pd.DataFrame()
    dsi_accuracy_df = pd.DataFrame()
    if not realised_outcomes_df.empty:
        calibration_df = compute_calibration_metrics(realised_outcomes_df, local_outcome_config)
        dsi_accuracy_df = compute_dsi_accuracy(realised_outcomes_df, local_outcome_config)

    monitored_segs = local_fairness_config.get('monitored_segment_cols', [])
    fairness_df = (
        compute_fairness_metrics(final_capacity_df, monitored_segs, local_fairness_config)
        if monitored_segs else pd.DataFrame()
    )

    explanations_df = generate_all_explanations(final_capacity_df, local_explainability_config)

    print('Snapshot rows stored:', len(snapshot_df))
    print('Evaluable outcome rows:', len(realised_outcomes_df))
    print('Calibration buckets:', len(calibration_df))
    print('DSI accuracy bands:', len(dsi_accuracy_df))
    print('Fairness segments flagged:', int(fairness_df['disparate_impact_flag'].sum()) if not fairness_df.empty else 0)
    print('Explanations generated:', len(explanations_df))

    return {
        'snapshot_df': snapshot_df,
        'realised_outcomes_df': realised_outcomes_df,
        'calibration_df': calibration_df,
        'dsi_accuracy_df': dsi_accuracy_df,
        'fairness_df': fairness_df,
        'explanations_df': explanations_df
    }
