"""
Unit tests for telecom_credit_engine.contracts.data_contracts.
Covers validate_dataframe_contract and validate_all_pipeline_inputs.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.contracts.data_contracts import (
    validate_dataframe_contract,
    validate_all_pipeline_inputs,
    ContractViolationError,
    LAYER1_FEATURES_CONTRACT,
    LAYER0_SCORES_CONTRACT,
    VW5_REASON_CODES_CONTRACT,
    VW6_CAP_ACTION_CONTRACT,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid DataFrames per contract
# ---------------------------------------------------------------------------

def _make_layer1(feature_dt='2026-04-01', msisdn='256700000001'):
    row = {col: (False if col.endswith('_flag') else 0.5)
           for col in LAYER1_FEATURES_CONTRACT}
    row['feature_dt'] = feature_dt
    row['subscriber_msisdn'] = msisdn
    row['outstanding_exposure_amt'] = 0.0
    row['active_lender_cnt_30d'] = 0
    row['disbursement_cnt_30d'] = 0
    row['wallet_inflow_amt_30d'] = 10000.0
    row['wallet_outflow_amt_30d'] = 5000.0
    row['spend_amt_30d'] = 3000.0
    row['wallet_active_days_30d'] = 20
    row['stacked_borrowing_flag_30d'] = 0
    row['repeated_borrowing_flag_30d'] = 0
    row['timing_manipulation_flag'] = 0
    row['suspicious_repayment_jump_flag'] = 0
    row['loan_cycling_flag'] = 0
    return pd.DataFrame([row])


def _make_layer0(feature_dt='2026-04-01', msisdn='256700000001'):
    row = {col: 0.5 for col in LAYER0_SCORES_CONTRACT}
    row['feature_dt'] = feature_dt
    row['subscriber_msisdn'] = msisdn
    return pd.DataFrame([row])


def _make_vw5(feature_dt='2026-04-01', msisdn='256700000001'):
    return pd.DataFrame([{
        'feature_dt': feature_dt,
        'subscriber_msisdn': msisdn,
        'primary_reason_code': 'PASS',
    }])


def _make_vw6(feature_dt='2026-04-01', msisdn='256700000001'):
    return pd.DataFrame([{
        'feature_dt': feature_dt,
        'subscriber_msisdn': msisdn,
        'conservative_credit_limit_v1': 10000.0,
        'recommended_action': 'MAINTAIN',
    }])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_valid_layer1_passes():
    violations = validate_dataframe_contract(_make_layer1(), 'layer1_features')
    assert violations == []


def test_valid_layer0_passes():
    violations = validate_dataframe_contract(_make_layer0(), 'layer0_scores')
    assert violations == []


def test_valid_vw5_passes():
    violations = validate_dataframe_contract(_make_vw5(), 'vw5_reason_codes')
    assert violations == []


def test_valid_vw6_passes():
    violations = validate_dataframe_contract(_make_vw6(), 'vw6_cap_action')
    assert violations == []


# ---------------------------------------------------------------------------
# Missing required column
# ---------------------------------------------------------------------------

def test_missing_required_col_raises_strict():
    df = _make_layer1().drop(columns=['subscriber_msisdn'])
    with pytest.raises(ContractViolationError, match='subscriber_msisdn'):
        validate_dataframe_contract(df, 'layer1_features', strict=True)


def test_missing_required_col_returns_violations_lenient():
    df = _make_layer1().drop(columns=['subscriber_msisdn'])
    violations = validate_dataframe_contract(df, 'layer1_features', strict=False)
    assert any('subscriber_msisdn' in v for v in violations)
    assert len(violations) >= 1


# ---------------------------------------------------------------------------
# Nullable vs non-nullable columns
# ---------------------------------------------------------------------------

def test_null_in_non_nullable_col_raises():
    df = _make_layer1()
    df.loc[0, 'outstanding_exposure_amt'] = np.nan
    with pytest.raises(ContractViolationError, match='outstanding_exposure_amt'):
        validate_dataframe_contract(df, 'layer1_features', strict=True)


def test_nullable_col_allows_null():
    # days_since_last_disbursement is nullable=True in LAYER1_FEATURES_CONTRACT
    df = _make_layer1()
    df.loc[0, 'days_since_last_disbursement'] = np.nan
    violations = validate_dataframe_contract(df, 'layer1_features', strict=True)
    assert not any('days_since_last_disbursement' in v for v in violations)


# ---------------------------------------------------------------------------
# Unknown contract name
# ---------------------------------------------------------------------------

def test_unknown_contract_name_raises():
    df = _make_layer1()
    with pytest.raises(ValueError, match='nonexistent'):
        validate_dataframe_contract(df, 'nonexistent')


# ---------------------------------------------------------------------------
# validate_all_pipeline_inputs
# ---------------------------------------------------------------------------

def test_validate_all_without_optional_dfs():
    result = validate_all_pipeline_inputs(
        _make_layer1(), _make_layer0(),
        vw5_df=None, vw6_df=None,
        strict=True,
    )
    assert set(result.keys()) == {'layer1_features', 'layer0_scores'}
    assert result['layer1_features'] == []
    assert result['layer0_scores'] == []


def test_validate_all_with_optional_dfs():
    result = validate_all_pipeline_inputs(
        _make_layer1(), _make_layer0(),
        vw5_df=_make_vw5(), vw6_df=_make_vw6(),
        strict=True,
    )
    assert set(result.keys()) == {'layer1_features', 'layer0_scores', 'vw5_reason_codes', 'vw6_cap_action'}
    assert all(v == [] for v in result.values())


def test_validate_all_propagates_strict_violation():
    bad_layer1 = _make_layer1().drop(columns=['wallet_inflow_amt_30d'])
    with pytest.raises(ContractViolationError):
        validate_all_pipeline_inputs(bad_layer1, _make_layer0(), strict=True)
