"""
Integration tests for diff_decisions from governance/decision_replay.
Tests the diffing logic directly without going through replay_decisions
(which depends on an active engine environment).
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.governance.decision_replay import diff_decisions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decisions_df(rows):
    return pd.DataFrame(rows)


def _base_row(msisdn='A', credit_limit=10000.0, action='MAINTAIN',
              status='approved_or_maintained', reason='PASS'):
    return {
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': msisdn,
        'CreditLimit': credit_limit,
        'selected_action': action,
        'decision_status': status,
        'primary_policy_reason': reason,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_identical_dfs_produce_empty_diff():
    df = _decisions_df([_base_row()])
    diff = diff_decisions(df, df.copy())
    assert diff.empty


def test_changed_credit_limit_detected():
    original = _decisions_df([_base_row(credit_limit=10000.0)])
    replayed = _decisions_df([_base_row(credit_limit=15000.0)])
    diff = diff_decisions(original, replayed)
    assert len(diff) == 1
    assert 'CreditLimit' in diff.iloc[0]['changed_fields']


def test_changed_action_detected():
    original = _decisions_df([_base_row(action='MAINTAIN')])
    replayed = _decisions_df([_base_row(action='REDUCE')])
    diff = diff_decisions(original, replayed)
    assert len(diff) == 1
    assert 'selected_action' in diff.iloc[0]['changed_fields']


def test_tolerance_absorbs_small_delta():
    original = _decisions_df([_base_row(credit_limit=10000.0)])
    replayed = _decisions_df([_base_row(credit_limit=10000.5)])
    diff = diff_decisions(original, replayed, tolerance_amt=1.0)
    assert diff.empty


def test_missing_subscriber_flagged():
    # Subscriber 'B' is only in the replayed set; diff_decisions should surface it.
    # The implementation reports field-level changes (NaN vs real value) for outer-join rows.
    original = _decisions_df([_base_row('A')])
    replayed = _decisions_df([_base_row('A'), _base_row('B')])
    diff = diff_decisions(original, replayed)
    missing = diff[diff['subscriber_msisdn'] == 'B']
    assert len(missing) == 1
    assert missing.iloc[0]['changed_fields'] != ''


def test_diff_output_columns_present_on_empty_diff():
    df = _decisions_df([_base_row()])
    diff = diff_decisions(df, df.copy())
    expected_cols = {
        'subscriber_msisdn', 'feature_dt',
        'original_CreditLimit', 'replayed_CreditLimit', 'delta_CreditLimit',
        'original_action', 'replayed_action',
        'original_status', 'replayed_status',
        'original_reason', 'replayed_reason',
        'changed_fields',
    }
    assert expected_cols.issubset(set(diff.columns))
