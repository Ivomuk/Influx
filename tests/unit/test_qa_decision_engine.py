"""
Unit tests for telecom_credit_engine.monitoring.qa_decision_engine_output.
Each test injects exactly one defect and asserts the corresponding check catches it.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.monitoring.qa_decision_engine_output import (
    qa_final_capacity,
    qa_policy_prefilter,
    run_qa_decision_engine_output,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid DataFrames
# ---------------------------------------------------------------------------

def _final_df(**overrides):
    df = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'CreditLimit': 5000.0,
        'selected_action': 'MAINTAIN',
        'decision_status': 'approved_or_maintained',
        'primary_policy_reason': 'PASS',
    }])
    for k, v in overrides.items():
        df.loc[0, k] = v
    return df


def _prefilter_df(**overrides):
    df = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'primary_policy_reason': 'PASS',
        'policy_restrict_flag': 0,
        'feasible_action_set': 'DECLINE,RESTRICT,REDUCE,MAINTAIN,INCREASE_SMALL,INCREASE_MEDIUM',
    }])
    for k, v in overrides.items():
        df.loc[0, k] = v
    return df


_CFG = {'policy_capacity_cap': 20000.0, 'stability_max_increase_pct': 0.25, 'stability_max_decrease_pct': 0.40}


# ---------------------------------------------------------------------------
# qa_final_capacity
# ---------------------------------------------------------------------------

def test_clean_output_all_pass():
    results = qa_final_capacity(_final_df(), _CFG)
    for key, fail_df in results.items():
        assert fail_df.empty, f'{key} unexpectedly failed'


def test_negative_credit_limit_caught():
    results = qa_final_capacity(_final_df(CreditLimit=-100.0), _CFG)
    assert not results['QA-DE-01_negative_credit_limit'].empty


def test_exceeds_policy_cap_caught():
    results = qa_final_capacity(_final_df(CreditLimit=25000.0), _CFG)
    assert not results['QA-DE-02_exceeds_policy_cap'].empty


def test_invalid_action_caught():
    results = qa_final_capacity(_final_df(selected_action='UNKNOWN_ACTION'), _CFG)
    assert not results['QA-DE-03_invalid_action'].empty


def test_invalid_decision_status_caught():
    results = qa_final_capacity(_final_df(decision_status='maybe'), _CFG)
    assert not results['QA-DE-04_invalid_decision_status'].empty


def test_duplicate_msisdn_caught():
    df = pd.concat([_final_df(), _final_df()], ignore_index=True)
    results = qa_final_capacity(df, _CFG)
    assert not results['QA-DE-05_duplicate_msisdn'].empty


def test_decline_with_nonzero_limit_caught():
    results = qa_final_capacity(_final_df(selected_action='DECLINE', CreditLimit=5000.0), _CFG)
    assert not results['QA-DE-06_restricted_with_nonzero_limit'].empty


def test_restrict_with_nonzero_limit_caught():
    results = qa_final_capacity(_final_df(selected_action='RESTRICT', CreditLimit=3000.0), _CFG)
    assert not results['QA-DE-06_restricted_with_nonzero_limit'].empty


def test_stability_upper_bound_violation_caught():
    prev = pd.DataFrame([{'subscriber_msisdn': '256700000001', 'CreditLimit': 10000.0}])
    # 14001 > 10000 * 1.25 = 12500 → violated
    results = qa_final_capacity(_final_df(CreditLimit=14001.0), _CFG, previous_capacity_df=prev)
    assert not results['QA-DE-07_stability_bounds_violated'].empty


def test_stability_check_skipped_without_prior():
    results = qa_final_capacity(_final_df(), _CFG, previous_capacity_df=None)
    assert 'QA-DE-07_stability_bounds_violated' not in results


# ---------------------------------------------------------------------------
# qa_policy_prefilter
# ---------------------------------------------------------------------------

def test_invalid_reason_code_caught():
    results = qa_policy_prefilter(_prefilter_df(primary_policy_reason='COMPLETELY_BOGUS'))
    assert not results['QA-DE-08_invalid_reason_code'].empty


def test_invalid_restrict_flag_caught():
    results = qa_policy_prefilter(_prefilter_df(policy_restrict_flag=2))
    assert not results['QA-DE-09_invalid_restrict_flag'].empty


def test_empty_feasible_set_caught():
    results = qa_policy_prefilter(_prefilter_df(feasible_action_set=''))
    assert not results['QA-DE-10_empty_feasible_set'].empty


# ---------------------------------------------------------------------------
# run_qa_decision_engine_output — integration over full outputs dict
# ---------------------------------------------------------------------------

def test_run_qa_all_pass_on_valid_engine_output(engine_outputs, default_config):
    results = run_qa_decision_engine_output(engine_outputs, default_config)
    failed = {k: v for k, v in results.items() if not v.empty}
    assert failed == {}, f'Unexpected QA failures: {list(failed.keys())}'
