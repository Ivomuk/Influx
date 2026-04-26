"""
Integration tests for the full run_credit_v1_decision_engine pipeline.
Runs the engine end-to-end and verifies output shape, constraints, and QA gates.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import (
    run_credit_v1_decision_engine,
    DECISION_ENGINE_CONFIG,
    ACTION_SPECS_DF,
)
from telecom_credit_engine.monitoring.qa_decision_engine_output import run_qa_decision_engine_output


VALID_STATUSES = {'approved_or_maintained', 'blocked_or_restricted', 'declined'}


# ---------------------------------------------------------------------------
# Output structure
# ---------------------------------------------------------------------------

def test_engine_produces_all_output_keys(engine_outputs):
    assert set(engine_outputs.keys()) == {
        'decision_base_df',
        'vw7_credit_v1_policy_prefilter',
        'vw8_credit_v1_tnv_action_evaluation',
        'vw9_credit_v1_final_capacity_output',
    }


def test_engine_output_row_count_matches_input(engine_outputs, layer1_df):
    vw9 = engine_outputs['vw9_credit_v1_final_capacity_output']
    assert len(vw9) == len(layer1_df)


# ---------------------------------------------------------------------------
# Business rule constraints
# ---------------------------------------------------------------------------

def test_engine_decision_status_valid_values(engine_outputs):
    statuses = set(engine_outputs['vw9_credit_v1_final_capacity_output']['decision_status'])
    assert statuses.issubset(VALID_STATUSES)


def test_engine_credit_limit_non_negative(engine_outputs):
    vw9 = engine_outputs['vw9_credit_v1_final_capacity_output']
    assert (vw9['CreditLimit'] >= 0).all()


def test_engine_credit_limit_respects_policy_cap(engine_outputs, default_config):
    vw9 = engine_outputs['vw9_credit_v1_final_capacity_output']
    assert vw9['CreditLimit'].max() <= default_config['policy_capacity_cap'] + 1


def test_engine_decline_restrict_have_zero_limit(layer1_df, layer0_df, default_config, action_specs_df):
    # Run on a subscriber who will be restricted (high fraud)
    l0_fraud = layer0_df.copy()
    l0_fraud.loc[0, 'fraud_abuse_risk_score_v1_rule'] = 0.95
    l0_fraud.loc[0, 'expected_repayment_probability_v1_rule'] = 0.01
    l0_fraud.loc[0, 'expected_future_transaction_margin_score_v1_rule'] = 0.01
    outputs = run_credit_v1_decision_engine(
        layer1_df, l0_fraud, config_dict=default_config, action_specs_input_df=action_specs_df
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    blocked = vw9[vw9['selected_action'].isin(['DECLINE', 'RESTRICT'])]
    assert (blocked['CreditLimit'] == 0).all()


# ---------------------------------------------------------------------------
# QA suite integration
# ---------------------------------------------------------------------------

def test_engine_qa_suite_all_pass(engine_outputs, default_config):
    results = run_qa_decision_engine_output(engine_outputs, default_config)
    failed = {k: v for k, v in results.items() if not v.empty}
    assert failed == {}, f'QA checks failed: {list(failed.keys())}'


# ---------------------------------------------------------------------------
# Stability smoothing end-to-end
# ---------------------------------------------------------------------------

def test_engine_stability_smoothing_end_to_end(layer1_df, layer0_df, default_config, action_specs_df):
    prev = pd.DataFrame([{
        'subscriber_msisdn': layer1_df.iloc[0]['subscriber_msisdn'],
        'CreditLimit': 10000.0,
    }])
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
        previous_capacity_df=prev,
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    max_inc = default_config['stability_max_increase_pct']
    upper = 10000.0 * (1 + max_inc)
    assert vw9.iloc[0]['CreditLimit'] <= upper + 1  # +1 for rounding


# ---------------------------------------------------------------------------
# vw5 override end-to-end
# ---------------------------------------------------------------------------

def test_engine_vw5_override_end_to_end(layer1_df, layer0_df, default_config, action_specs_df):
    vw5 = pd.DataFrame([{
        'feature_dt': layer1_df.iloc[0]['feature_dt'],
        'subscriber_msisdn': layer1_df.iloc[0]['subscriber_msisdn'],
        'primary_reason_code': 'HIGH_ACTIVE_LENDER_COUNT',
    }])
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
        vw5_reason_codes_df=vw5,
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    assert vw9.iloc[0]['primary_policy_reason'] == 'HIGH_ACTIVE_LENDER_COUNT'
