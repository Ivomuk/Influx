"""
Parametrized contract schema regression tests.
Verifies that all 5 named contracts enforce their field requirements correctly.
These tests are pure schema guards — they should never be removed.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.contracts.data_contracts import (
    CONTRACTS,
    ContractViolationError,
    validate_dataframe_contract,
)


# ---------------------------------------------------------------------------
# Build a minimal valid DataFrame for any contract
# ---------------------------------------------------------------------------

def _minimal_valid_df(contract_name):
    contract = CONTRACTS[contract_name]
    row = {}
    for col, spec in contract.items():
        if col == 'feature_dt':
            row[col] = pd.Timestamp('2026-04-01')
        elif col == 'subscriber_msisdn':
            row[col] = '256700000001'
        elif col in ('primary_reason_code', 'recommended_action', 'selected_action', 'decision_status'):
            row[col] = 'PASS'
        else:
            row[col] = 0.0 if spec['nullable'] else 1.0
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# Parametrize over all contract names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('contract_name', list(CONTRACTS.keys()))
def test_minimal_valid_df_passes(contract_name):
    df = _minimal_valid_df(contract_name)
    violations = validate_dataframe_contract(df, contract_name, strict=True)
    assert violations == [], f'{contract_name}: unexpected violations: {violations}'


@pytest.mark.parametrize('contract_name', list(CONTRACTS.keys()))
def test_required_cols_are_present(contract_name):
    contract = CONTRACTS[contract_name]
    required = [col for col, spec in contract.items() if spec['required']]
    assert len(required) > 0, f'{contract_name}: no required columns defined'


@pytest.mark.parametrize('contract_name', list(CONTRACTS.keys()))
def test_nullable_cols_accept_nulls(contract_name):
    contract = CONTRACTS[contract_name]
    nullable_cols = [col for col, spec in contract.items() if spec['nullable']]
    if not nullable_cols:
        pytest.skip(f'{contract_name} has no nullable columns')
    df = _minimal_valid_df(contract_name)
    df.loc[0, nullable_cols[0]] = np.nan
    violations = validate_dataframe_contract(df, contract_name, strict=True)
    assert not any(nullable_cols[0] in v for v in violations)


@pytest.mark.parametrize('contract_name', list(CONTRACTS.keys()))
def test_non_nullable_required_cols_reject_nulls(contract_name):
    contract = CONTRACTS[contract_name]
    non_nullable_required = [
        col for col, spec in contract.items()
        if spec['required'] and not spec['nullable']
        and col not in ('feature_dt', 'subscriber_msisdn')
    ]
    if not non_nullable_required:
        pytest.skip(f'{contract_name} has no non-nullable non-key required columns')
    df = _minimal_valid_df(contract_name)
    col_to_null = non_nullable_required[0]
    df.loc[0, col_to_null] = np.nan
    with pytest.raises(ContractViolationError, match=col_to_null):
        validate_dataframe_contract(df, contract_name, strict=True)


# ---------------------------------------------------------------------------
# All contracts share the two key columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('contract_name', list(CONTRACTS.keys()))
def test_all_contracts_have_key_columns(contract_name):
    contract = CONTRACTS[contract_name]
    assert 'feature_dt' in contract, f'{contract_name}: missing feature_dt'
    assert 'subscriber_msisdn' in contract, f'{contract_name}: missing subscriber_msisdn'
    assert contract['feature_dt']['required']
    assert contract['subscriber_msisdn']['required']


# ---------------------------------------------------------------------------
# Specific structural assertions
# ---------------------------------------------------------------------------

def test_contracts_dict_has_five_entries():
    assert len(CONTRACTS) == 5


def test_final_capacity_has_decision_status():
    assert 'decision_status' in CONTRACTS['final_capacity']
    assert CONTRACTS['final_capacity']['decision_status']['required']
    assert not CONTRACTS['final_capacity']['decision_status']['nullable']


def test_layer1_nullable_columns_are_correct():
    nullable = {col for col, spec in CONTRACTS['layer1_features'].items() if spec['nullable']}
    expected_nullable = {
        'days_since_last_disbursement',
        'repayment_ratio_30d',
        'repayment_ratio_trend_7d',
        'wallet_inflow_trend_7d_vs_90d',
    }
    assert expected_nullable.issubset(nullable)
