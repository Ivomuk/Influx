# Probability-of-Default (PD) model — feature extraction.
#
# Shared by scoring (pd_model.py) and training (pd_model_training.py) so both
# paths build the exact same feature matrix from layer1_df. Feature set is
# restricted to columns guaranteed by LAYER1_FEATURES_CONTRACT plus one
# derived ratio, so scoring works against both the full vw3 output and the
# narrower contract-minimum fixtures used in tests.

import numpy as np
import pandas as pd

PD_FEATURE_NAMES = [
    'repayment_ratio_30d',
    'repayment_ratio_trend_7d',
    'wallet_inflow_trend_7d_vs_90d',
    'active_lender_cnt_30d',
    'disbursement_cnt_30d',
    'days_since_last_disbursement',
    'exposure_to_inflow_ratio',
    'stacked_borrowing_flag_30d',
    'repeated_borrowing_flag_30d',
    'timing_manipulation_flag',
    'suspicious_repayment_jump_flag',
    'loan_cycling_flag',
]


def _compute_exposure_to_inflow_ratio(layer1_df, default_value):
    if 'outstanding_exposure_amt' not in layer1_df.columns or 'wallet_inflow_amt_30d' not in layer1_df.columns:
        return pd.Series(default_value, index=layer1_df.index)
    inflow = layer1_df['wallet_inflow_amt_30d']
    ratio = np.where(
        inflow > 0,
        layer1_df['outstanding_exposure_amt'] / inflow,
        default_value,
    )
    return pd.Series(ratio, index=layer1_df.index).fillna(default_value)


def extract_pd_features(layer1_df, feature_defaults, feature_names=None):
    """
    Builds the PD model feature matrix from layer1_df.

    Any feature in *feature_names* absent from layer1_df.columns, or null in
    a present row, is filled with feature_defaults[feature_name]. This makes
    scoring tolerant of both the full vw3_credit_v1_layer1_features output
    and the narrower LAYER1_FEATURES_CONTRACT-only fixtures used in tests.

    Returns a DataFrame with columns [feature_dt, subscriber_msisdn] + feature_names.
    """
    names = feature_names or PD_FEATURE_NAMES
    out = pd.DataFrame({
        'feature_dt': layer1_df['feature_dt'],
        'subscriber_msisdn': layer1_df['subscriber_msisdn'],
    })

    for name in names:
        default_value = feature_defaults[name]
        if name == 'exposure_to_inflow_ratio':
            out[name] = _compute_exposure_to_inflow_ratio(layer1_df, default_value)
            continue
        if name in layer1_df.columns:
            out[name] = pd.to_numeric(layer1_df[name], errors='coerce').fillna(default_value)
        else:
            out[name] = default_value

    return out
