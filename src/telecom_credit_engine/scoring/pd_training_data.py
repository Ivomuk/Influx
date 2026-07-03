# Synthetic PD training data generator.
#
# The repo has no real historical default outcomes yet — did_default_flag is
# only populated once lender DEFAULT feedback events accumulate through
# dim6_outcome_tracking.compute_realised_outcomes(). This module produces a
# placeholder labeled dataset that rehearses the REAL labeling pipeline:
# each synthetic subscriber holds 1-3 loans across products/lenders, each
# loan may default (with amount / days_past_due / loan_status drawn to
# exercise the materiality and UTP limbs), and the subscriber-level
# did_default_flag is derived by the same identify_default_events() +
# obligor-level OR used on real lender feedback — so training and serving
# label logic cannot diverge. Replace the event source with real
# compute_realised_outcomes() output once enough lender feedback has
# accumulated in production.

import numpy as np
import pandas as pd

from telecom_credit_engine.monitoring.dim6_outcome_tracking import identify_default_events
from telecom_credit_engine.scoring.pd_features import PD_FEATURE_NAMES


def _simulate_subscriber_default_flag(rng, base_default_probability, label_config):
    """
    Simulates one subscriber's multi-loan portfolio and returns
    (did_default_flag, n_loans) via the shared default definition.

    Deliberately generates sub-threshold small defaults, early-stage (<90 DPD)
    defaults, missing days_past_due, and occasional UTP write-offs so the
    materiality and unlikeliness-to-pay limbs are all exercised.
    """
    n_loans = int(rng.integers(1, 4))
    events = []
    for _ in range(n_loans):
        loan_default_probability = float(np.clip(base_default_probability * rng.uniform(0.7, 1.3), 0.0, 1.0))
        if rng.random() >= loan_default_probability:
            continue

        # Defaulted loan: draw severity and reporting completeness.
        event_amount = float(rng.gamma(2.0, 400.0))  # some draws land below the abs floor

        dpd_draw = rng.random()
        if dpd_draw < 0.20:
            days_past_due = np.nan                       # lender did not report the optional field
        elif dpd_draw < 0.45:
            days_past_due = float(rng.integers(15, 90))  # early-stage arrears — immaterial under 90-DPD
        else:
            days_past_due = float(rng.integers(90, 240))

        # Occasionally the lender reports an unlikeliness-to-pay status,
        # which triggers default regardless of amount/DPD.
        loan_status = 'WRITTEN_OFF' if rng.random() < 0.10 else None

        events.append({
            'msisdn': 'synthetic',
            'is_default': 1,
            'event_amount': event_amount,
            'days_past_due': days_past_due,
            'loan_status': loan_status,
        })

    if not events:
        return 0, n_loans

    triggers = identify_default_events(pd.DataFrame(events), label_config, credit_limit=None)
    return int(len(triggers) > 0), n_loans


def generate_synthetic_pd_training_data(n_rows=2000, seed=42, label_config=None):
    """
    Returns a DataFrame with [feature_dt, subscriber_msisdn] + PD_FEATURE_NAMES
    + did_default_flag. Features drive a latent per-subscriber risk level;
    per-loan defaults are simulated from it and merged into the obligor-level
    label via identify_default_events() (see module docstring).

    label_config: materiality/UTP thresholds for identify_default_events();
    defaults to that function's standard values when None.
    """
    rng = np.random.default_rng(seed)
    label_config = label_config or {}

    repayment_ratio_30d = np.clip(rng.beta(5, 2, n_rows), 0.0, 1.0)
    repayment_ratio_trend_7d = rng.normal(0.0, 0.15, n_rows)
    wallet_inflow_trend_7d_vs_90d = np.clip(rng.normal(1.0, 0.4, n_rows), 0.0, None)
    active_lender_cnt_30d = rng.poisson(1.2, n_rows)
    disbursement_cnt_30d = rng.poisson(2.0, n_rows)
    days_since_last_disbursement = rng.integers(0, 60, n_rows)
    exposure_to_inflow_ratio = np.clip(rng.gamma(1.5, 0.4, n_rows), 0.0, None)
    stacked_borrowing_flag_30d = (active_lender_cnt_30d >= 2).astype(int)
    repeated_borrowing_flag_30d = (disbursement_cnt_30d >= 4).astype(int)
    timing_manipulation_flag = rng.binomial(1, 0.08, n_rows)
    suspicious_repayment_jump_flag = rng.binomial(1, 0.06, n_rows)
    loan_cycling_flag = rng.binomial(1, 0.05, n_rows)

    features_df = pd.DataFrame({
        'repayment_ratio_30d': repayment_ratio_30d,
        'repayment_ratio_trend_7d': repayment_ratio_trend_7d,
        'wallet_inflow_trend_7d_vs_90d': wallet_inflow_trend_7d_vs_90d,
        'active_lender_cnt_30d': active_lender_cnt_30d,
        'disbursement_cnt_30d': disbursement_cnt_30d,
        'days_since_last_disbursement': days_since_last_disbursement,
        'exposure_to_inflow_ratio': exposure_to_inflow_ratio,
        'stacked_borrowing_flag_30d': stacked_borrowing_flag_30d,
        'repeated_borrowing_flag_30d': repeated_borrowing_flag_30d,
        'timing_manipulation_flag': timing_manipulation_flag,
        'suspicious_repayment_jump_flag': suspicious_repayment_jump_flag,
        'loan_cycling_flag': loan_cycling_flag,
    })[PD_FEATURE_NAMES]

    # Latent per-subscriber risk — same risk direction as the vw4 rules:
    # lower repayment ratio, declining trend, higher exposure-to-inflow, and
    # active anti-gaming flags all push default probability up.
    latent = (
        -1.60
        - 4.00 * (features_df['repayment_ratio_30d'] - 0.5)
        - 1.00 * features_df['repayment_ratio_trend_7d']
        - 0.50 * (features_df['wallet_inflow_trend_7d_vs_90d'] - 1.0)
        + 0.30 * features_df['active_lender_cnt_30d']
        + 0.15 * features_df['disbursement_cnt_30d']
        + 0.80 * features_df['exposure_to_inflow_ratio']
        + 0.70 * features_df['stacked_borrowing_flag_30d']
        + 0.50 * features_df['repeated_borrowing_flag_30d']
        + 0.90 * features_df['timing_manipulation_flag']
        + 0.60 * features_df['suspicious_repayment_jump_flag']
        + 1.00 * features_df['loan_cycling_flag']
        + rng.normal(0.0, 0.5, n_rows)
    )
    base_default_probability = 1.0 / (1.0 + np.exp(-latent))

    did_default_flag = np.zeros(n_rows, dtype=int)
    for i in range(n_rows):
        did_default_flag[i], _ = _simulate_subscriber_default_flag(
            rng, base_default_probability.iloc[i], label_config
        )

    training_df = features_df.copy()
    training_df.insert(0, 'subscriber_msisdn', [f'25670{i:07d}' for i in range(n_rows)])
    training_df.insert(0, 'feature_dt', pd.Timestamp('2026-01-01'))
    training_df['did_default_flag'] = did_default_flag

    return training_df
