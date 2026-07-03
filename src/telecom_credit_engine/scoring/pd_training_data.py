# Synthetic PD training data generator.
#
# The repo has no real historical default outcomes yet — did_default_flag is
# only populated once lender DEFAULT feedback events accumulate through
# dim6_outcome_tracking.compute_realised_outcomes(). This module produces a
# placeholder labeled dataset, generatively consistent with the risk
# direction already encoded in vw4_credit_v1_layer0_scores.sql's rules
# (low repayment ratio, high exposure-to-inflow, active anti-gaming flags ->
# higher default probability), so the training pipeline is runnable end to
# end today. Replace with real compute_realised_outcomes() output once
# enough lender feedback has accumulated in production.

import numpy as np
import pandas as pd

from telecom_credit_engine.scoring.pd_features import PD_FEATURE_NAMES


def generate_synthetic_pd_training_data(n_rows=2000, seed=42):
    """
    Returns a DataFrame with [feature_dt, subscriber_msisdn] + PD_FEATURE_NAMES
    + did_default_flag, generated from a logistic latent process so the
    features are meaningfully (not randomly) associated with the label.
    """
    rng = np.random.default_rng(seed)

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

    # Generative logistic process — same risk direction as the vw4 rules:
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
    default_probability = 1.0 / (1.0 + np.exp(-latent))
    did_default_flag = rng.binomial(1, default_probability)

    training_df = features_df.copy()
    training_df.insert(0, 'subscriber_msisdn', [f'25670{i:07d}' for i in range(n_rows)])
    training_df.insert(0, 'feature_dt', pd.Timestamp('2026-01-01'))
    training_df['did_default_flag'] = did_default_flag

    return training_df
