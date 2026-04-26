"""
Unit tests for telecom_credit_engine.interfaces.external_lender_interface.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.interfaces.external_lender_interface import build_external_lender_interface


def _sample_df(**overrides):
    df = pd.DataFrame([
        {
            'subscriber_msisdn': '256700000003',
            'CreditLimit': 10000.0,
            'output_version': 'credit_v1_prod',
            'validity_days': 14,
        },
        {
            'subscriber_msisdn': '256700000001',
            'CreditLimit': 5000.0,
            'output_version': 'credit_v1_prod',
            'validity_days': 7,
        },
        {
            'subscriber_msisdn': '256700000002',
            'CreditLimit': 0.0,
            'output_version': 'credit_v1_prod',
            'validity_days': 7,
        },
    ])
    for k, v in overrides.items():
        df[k] = v
    return df


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

def test_required_fields_only_has_two_columns():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=False)
    assert list(result.columns) == ['MSISDN', 'CreditLimit']


def test_optional_fields_has_five_columns():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=True)
    assert set(result.columns) == {'MSISDN', 'CreditLimit', 'validity_period_days', 'decision_timestamp', 'policy_version'}


def test_validity_days_renamed_to_validity_period_days():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=True)
    assert 'validity_days' not in result.columns
    assert 'validity_period_days' in result.columns


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def test_output_sorted_by_msisdn():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=False)
    assert list(result['MSISDN']) == sorted(result['MSISDN'].tolist())


# ---------------------------------------------------------------------------
# Column renaming
# ---------------------------------------------------------------------------

def test_subscriber_msisdn_renamed_to_msisdn():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=False)
    assert 'MSISDN' in result.columns
    assert 'subscriber_msisdn' not in result.columns


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_missing_required_col_raises():
    df = _sample_df().drop(columns=['CreditLimit'])
    with pytest.raises(ValueError, match='CreditLimit'):
        build_external_lender_interface(df)


# ---------------------------------------------------------------------------
# Timestamp handling
# ---------------------------------------------------------------------------

def test_custom_decision_timestamp_applied():
    result = build_external_lender_interface(
        _sample_df(), include_optional_fields=True, decision_timestamp='2026-04-23'
    )
    assert all(str(ts).startswith('2026-04-23') for ts in result['decision_timestamp'])


def test_utc_timestamp_set_when_none():
    result = build_external_lender_interface(_sample_df(), include_optional_fields=True, decision_timestamp=None)
    assert result['decision_timestamp'].notna().all()


# ---------------------------------------------------------------------------
# Policy version fallback
# ---------------------------------------------------------------------------

def test_policy_version_from_output_version():
    result = build_external_lender_interface(
        _sample_df(), include_optional_fields=True, policy_version_col='output_version'
    )
    assert all(result['policy_version'] == 'credit_v1_prod')


def test_missing_policy_version_col_defaults_to_unknown():
    df = _sample_df().drop(columns=['output_version'])
    result = build_external_lender_interface(df, include_optional_fields=True, policy_version_col='output_version')
    assert all(result['policy_version'] == 'unknown_policy_version')
