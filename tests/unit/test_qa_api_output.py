"""
Unit tests for telecom_credit_engine.monitoring.qa_api_output_contract.
Each test injects exactly one defect per QA check.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from telecom_credit_engine.monitoring.qa_api_output_contract import (
    qa_eligibility_response,
    qa_state_transitions,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid DataFrames
# ---------------------------------------------------------------------------

def _eligibility_df(**overrides):
    df = pd.DataFrame([{
        'MSISDN': '256700000001',
        'CreditLimit': 5000,
        'validity_period_days': 14,
        'decision_timestamp': '2026-04-01T12:00:00',
        'policy_version': 'credit_v1_prod',
        'decision_status': 'approved_or_maintained',
        'is_stale': 0,
        'batch_latency_ms': 50.0,
    }])
    for k, v in overrides.items():
        df.loc[0, k] = v
    return df


def _state_df(**overrides):
    df = pd.DataFrame([{
        'feature_dt': '2026-04-01',
        'subscriber_msisdn': '256700000001',
        'operating_state': 'Healthy',
        'persistence_block_applied': 0,
        'state_change_dt': '2026-03-01',
    }])
    for k, v in overrides.items():
        df.loc[0, k] = v
    return df


# ---------------------------------------------------------------------------
# qa_eligibility_response — happy path
# ---------------------------------------------------------------------------

def test_clean_eligibility_df_all_pass():
    results = qa_eligibility_response(_eligibility_df())
    failed = {k: v for k, v in results.items()
              if isinstance(v, pd.DataFrame) and not v.empty}
    assert failed == {}


# ---------------------------------------------------------------------------
# qa_eligibility_response — individual defect checks
# ---------------------------------------------------------------------------

def test_missing_required_column_returns_early():
    df = _eligibility_df().drop(columns=['MSISDN'])
    results = qa_eligibility_response(df)
    assert 'QA-API-00_missing_columns' in results


def test_negative_credit_limit_caught():
    results = qa_eligibility_response(_eligibility_df(CreditLimit=-1))
    assert not results['QA-API-01_negative_credit_limit'].empty


def test_invalid_decision_status_caught():
    results = qa_eligibility_response(_eligibility_df(decision_status='maybe'))
    assert not results['QA-API-02_invalid_decision_status'].empty


def test_no_data_nonzero_limit_caught():
    results = qa_eligibility_response(_eligibility_df(decision_status='NO_DATA', CreditLimit=500))
    assert not results['QA-API-07_no_data_nonzero_limit'].empty


def test_declined_nonzero_limit_caught():
    results = qa_eligibility_response(_eligibility_df(decision_status='declined', CreditLimit=100))
    assert not results['QA-API-08_restricted_nonzero_limit'].empty


def test_blocked_nonzero_limit_caught():
    results = qa_eligibility_response(_eligibility_df(decision_status='blocked_or_restricted', CreditLimit=100))
    assert not results['QA-API-08_restricted_nonzero_limit'].empty


# ---------------------------------------------------------------------------
# qa_state_transitions
# ---------------------------------------------------------------------------

def test_valid_state_df_all_pass():
    results = qa_state_transitions(_state_df())
    failed = {k: v for k, v in results.items()
              if isinstance(v, pd.DataFrame) and not v.empty}
    assert failed == {}


def test_invalid_operating_state_caught():
    results = qa_state_transitions(_state_df(operating_state='Unknown'))
    assert not results['QA-ST-01_invalid_operating_state'].empty


def test_future_state_change_dt_caught():
    results = qa_state_transitions(_state_df(
        feature_dt='2026-04-01',
        state_change_dt='2026-05-01',  # after feature_dt
    ))
    assert not results['QA-ST-03_state_change_dt_future'].empty


def test_duplicate_subscriber_state_caught():
    df = pd.concat([_state_df(), _state_df()], ignore_index=True)
    results = qa_state_transitions(df)
    assert not results['QA-ST-04_duplicate_msisdn'].empty
