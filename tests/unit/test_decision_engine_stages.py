"""
Unit tests for individual stage functions of run_credit_v1_decision_engine.
Tests each pipeline stage in isolation without running the full engine.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import (
    prepare_decision_base,
    build_policy_prefilter,
    evaluate_tnv_actions,
    select_final_capacity_output,
    run_credit_v1_decision_engine,
    DECISION_ENGINE_CONFIG,
    ACTION_SPECS_DF,
)


# ---------------------------------------------------------------------------
# Helpers — build minimal DataFrames per stage
# ---------------------------------------------------------------------------

def _l1_row(**overrides):
    row = {
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'outstanding_exposure_amt': 0.0,
        'active_lender_cnt_30d': 0,
        'disbursement_cnt_30d': 0,
        'days_since_last_disbursement': None,
        'repayment_ratio_30d': 0.90,
        'repayment_ratio_trend_7d': 0.05,
        'wallet_inflow_amt_30d': 50000.0,
        'wallet_outflow_amt_30d': 20000.0,
        'spend_amt_30d': 15000.0,
        'wallet_active_days_30d': 25,
        'wallet_inflow_trend_7d_vs_90d': 0.05,
        'stacked_borrowing_flag_30d': 0,
        'repeated_borrowing_flag_30d': 0,
        'timing_manipulation_flag': 0,
        'suspicious_repayment_jump_flag': 0,
        'loan_cycling_flag': 0,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _l0_row(**overrides):
    row = {
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'expected_repayment_probability_v1_rule': 0.85,
        'expected_credit_loss_rate_v1_rule': 0.05,
        'churn_cooling_probability_v1_rule': 0.10,
        'expected_future_transaction_margin_score_v1_rule': 0.70,
        'expected_treatment_cost_score_v1_rule': 0.02,
        'customer_lifetime_value_contribution_v1_rule': 0.80,
        'debt_stress_index_v1': 20.0,
        'fraud_abuse_risk_score_v1_rule': 0.05,
        'behavior_consistency_score_v1_rule': 0.90,
        'identity_confidence_score_v1_rule': 0.95,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _base(l1_overrides=None, l0_overrides=None):
    l1 = _l1_row(**(l1_overrides or {}))
    l0 = _l0_row(**(l0_overrides or {}))
    return prepare_decision_base(l1, l0)


def _cfg(**overrides):
    cfg = DECISION_ENGINE_CONFIG.copy()
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# prepare_decision_base
# ---------------------------------------------------------------------------

def test_prepare_decision_base_joins_correctly():
    merged = _base()
    assert len(merged) == 1
    assert 'subscriber_msisdn' in merged.columns
    assert 'expected_repayment_probability_v1_rule' in merged.columns


def test_prepare_decision_base_missing_col_raises():
    l1 = _l1_row().drop(columns=['wallet_inflow_amt_30d'])
    l0 = _l0_row()
    with pytest.raises(ValueError, match='wallet_inflow_amt_30d'):
        prepare_decision_base(l1, l0)


def test_prepare_decision_base_empty_merge_raises():
    l1 = _l1_row(subscriber_msisdn='A')
    l0 = _l0_row(subscriber_msisdn='B')
    with pytest.raises(ValueError, match='empty'):
        prepare_decision_base(l1, l0)


# ---------------------------------------------------------------------------
# build_policy_prefilter — restrict conditions
# ---------------------------------------------------------------------------

def test_build_policy_prefilter_high_fraud_restricts():
    base = _base(l0_overrides={'fraud_abuse_risk_score_v1_rule': 0.90})
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['policy_restrict_flag'] == 1
    assert pf.iloc[0]['primary_policy_reason'] == 'HIGH_FRAUD_ABUSE_RISK'


def test_build_policy_prefilter_low_identity_confidence_restricts():
    base = _base(l0_overrides={'identity_confidence_score_v1_rule': 0.50})
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['policy_restrict_flag'] == 1
    assert pf.iloc[0]['primary_policy_reason'] == 'LOW_IDENTITY_CONFIDENCE'


def test_build_policy_prefilter_severe_dsi_restricts():
    base = _base(l0_overrides={'debt_stress_index_v1': 90.0})
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['policy_restrict_flag'] == 1
    assert pf.iloc[0]['primary_policy_reason'] == 'SEVERE_DSI'


def test_build_policy_prefilter_timing_manipulation_restricts():
    base = _base(l1_overrides={'timing_manipulation_flag': 1})
    pf = build_policy_prefilter(base, _cfg(block_timing_manipulation=True))
    assert pf.iloc[0]['policy_restrict_flag'] == 1
    assert pf.iloc[0]['primary_policy_reason'] == 'TIMING_MANIPULATION_DETECTED'


def test_build_policy_prefilter_timing_manipulation_ignored_when_disabled():
    base = _base(l1_overrides={'timing_manipulation_flag': 1})
    pf = build_policy_prefilter(base, _cfg(block_timing_manipulation=False))
    assert pf.iloc[0]['policy_restrict_flag'] == 0


def test_build_policy_prefilter_thin_file_flagged():
    base = _base(l1_overrides={'wallet_active_days_30d': 2})
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['thin_file_flag'] == 1
    assert pf.iloc[0]['allow_increase_medium'] == 0


def test_build_policy_prefilter_healthy_subscriber_no_restrict():
    base = _base()
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['policy_restrict_flag'] == 0
    assert pf.iloc[0]['primary_policy_reason'] == 'PASS'
    assert pf.iloc[0]['allow_increase_medium'] == 1


def test_build_policy_prefilter_vw5_overrides_reason():
    base = _base()
    vw5 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'primary_reason_code': 'VERY_LOW_REPAYMENT_RATIO',
    }])
    pf = build_policy_prefilter(base, _cfg(), vw5_reason_codes_df=vw5)
    assert pf.iloc[0]['primary_policy_reason'] == 'VERY_LOW_REPAYMENT_RATIO'


def test_build_policy_prefilter_vw5_eligible_baseline_not_overridden():
    base = _base()
    vw5 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        'primary_reason_code': 'ELIGIBLE_BASELINE',
    }])
    pf = build_policy_prefilter(base, _cfg(), vw5_reason_codes_df=vw5)
    assert pf.iloc[0]['primary_policy_reason'] == 'PASS'


# ---------------------------------------------------------------------------
# evaluate_tnv_actions
# ---------------------------------------------------------------------------

def test_evaluate_tnv_produces_rows_for_each_feasible_action():
    base = _base()
    pf = build_policy_prefilter(base, _cfg())
    tnv = evaluate_tnv_actions(pf, ACTION_SPECS_DF, _cfg())
    # Healthy subscriber: all 6 actions are feasible
    assert len(tnv) == 6
    assert set(tnv['action']) == {'DECLINE', 'RESTRICT', 'REDUCE', 'MAINTAIN', 'INCREASE_SMALL', 'INCREASE_MEDIUM'}


def test_evaluate_tnv_restricted_subscriber_has_fewer_actions():
    base = _base(l0_overrides={'fraud_abuse_risk_score_v1_rule': 0.90})
    pf = build_policy_prefilter(base, _cfg())
    tnv = evaluate_tnv_actions(pf, ACTION_SPECS_DF, _cfg())
    # Restricted: no MAINTAIN, INCREASE_SMALL, INCREASE_MEDIUM
    assert 'MAINTAIN' not in tnv['action'].values
    assert 'INCREASE_SMALL' not in tnv['action'].values
    assert 'INCREASE_MEDIUM' not in tnv['action'].values


# ---------------------------------------------------------------------------
# select_final_capacity_output — decision status and caps
# ---------------------------------------------------------------------------

def _make_tnv_df(msisdn, action, expected_tnv, target_capacity_raw=10000.0):
    return pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': msisdn,
        'primary_policy_reason': 'PASS',
        'action': action,
        'target_capacity_raw': target_capacity_raw,
        'expected_tnv': expected_tnv,
        'stability_multiplier': 1.0,
        'update_direction': 'maintain',
        'validity_days': 14,
    }])


def test_select_final_capacity_decline_when_tnv_negative():
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=-50.0)
    result = select_final_capacity_output(tnv, _cfg())
    assert result.iloc[0]['selected_action'] == 'DECLINE'
    assert result.iloc[0]['CreditLimit'] == 0
    assert result.iloc[0]['decision_status'] == 'declined'


def test_select_final_capacity_restrict_maps_to_blocked_status():
    # Force RESTRICT as best action with positive TNV by constructing TNV df directly
    tnv = _make_tnv_df('A', 'RESTRICT', expected_tnv=10.0, target_capacity_raw=0.0)
    result = select_final_capacity_output(tnv, _cfg())
    assert result.iloc[0]['selected_action'] == 'RESTRICT'
    assert result.iloc[0]['decision_status'] == 'blocked_or_restricted'


def test_select_final_capacity_positive_tnv_sets_approved_status():
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0)
    result = select_final_capacity_output(tnv, _cfg())
    assert result.iloc[0]['selected_action'] == 'INCREASE_MEDIUM'
    assert result.iloc[0]['decision_status'] == 'approved_or_maintained'


def test_select_final_capacity_credit_limit_capped_at_policy_cap():
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0, target_capacity_raw=30000.0)
    result = select_final_capacity_output(tnv, _cfg(policy_capacity_cap=20000.0))
    assert result.iloc[0]['CreditLimit'] <= 20000.0


def test_stability_smoothing_caps_increase():
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0, target_capacity_raw=30000.0)
    prev = pd.DataFrame([{'subscriber_msisdn': 'A', 'CreditLimit': 10000.0}])
    result = select_final_capacity_output(tnv, _cfg(stability_max_increase_pct=0.25), previous_capacity_df=prev)
    # policy cap applied first → min(30000, 20000) = 20000; stability: min(20000, 10000*1.25) = 12500
    assert result.iloc[0]['CreditLimit'] <= 12500.0


def test_stability_smoothing_floors_decrease():
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=100.0, target_capacity_raw=1000.0)
    prev = pd.DataFrame([{'subscriber_msisdn': 'A', 'CreditLimit': 10000.0}])
    result = select_final_capacity_output(tnv, _cfg(stability_max_decrease_pct=0.40), previous_capacity_df=prev)
    # floor = 10000 * 0.60 = 6000; proposed = 1000 * 1.0 = 1000 → 6000
    assert result.iloc[0]['CreditLimit'] >= 6000.0


def test_first_time_subscriber_no_smoothing():
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=100.0, target_capacity_raw=8000.0)
    prev = pd.DataFrame([{'subscriber_msisdn': 'A', 'CreditLimit': 0.0}])
    result = select_final_capacity_output(tnv, _cfg(), previous_capacity_df=prev)
    # prev=0 → no bounds applied; CreditLimit = 8000 * stability_multiplier=1.0
    assert result.iloc[0]['CreditLimit'] == pytest.approx(8000.0)


def test_vw6_conservative_cap_applied():
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0, target_capacity_raw=18000.0)
    vw6 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': 'A',
        'conservative_credit_limit_v1': 5000.0,
        'recommended_action': 'MAINTAIN',
    }])
    result = select_final_capacity_output(tnv, _cfg(), vw6_cap_action_df=vw6)
    assert result.iloc[0]['CreditLimit'] <= 5000.0


def test_vw6_cannot_increase_credit_limit():
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=100.0, target_capacity_raw=5000.0)
    vw6 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': 'A',
        'conservative_credit_limit_v1': 99000.0,
        'recommended_action': 'MAINTAIN',
    }])
    result = select_final_capacity_output(tnv, _cfg(), vw6_cap_action_df=vw6)
    assert result.iloc[0]['CreditLimit'] <= 5000.0


# ---------------------------------------------------------------------------
# run_credit_v1_decision_engine — smoke tests
# ---------------------------------------------------------------------------

def test_circuit_breaker_scales_policy_cap():
    l1 = _l1_row()
    l0 = _l0_row()
    # With cb=0.5, effective cap = 20000 * 0.5 = 10000
    outputs = run_credit_v1_decision_engine(l1, l0, config_dict=_cfg(), circuit_breaker_multiplier=0.5)
    final = outputs['vw9_credit_v1_final_capacity_output']
    assert final.iloc[0]['CreditLimit'] <= 10000.0


def test_decision_status_three_values_covered(layer1_df, layer0_df, default_config, action_specs_df):
    # Use full engine on healthy sub → approved_or_maintained
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    valid = {'approved_or_maintained', 'blocked_or_restricted', 'declined'}
    assert all(s in valid for s in vw9['decision_status'])


def test_decline_zeroed_after_stability_smoothing():
    """DECLINE must produce CreditLimit=0 even when prior limit would clip it up."""
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=-50.0, target_capacity_raw=10000.0)
    prev = pd.DataFrame([{'subscriber_msisdn': 'A', 'CreditLimit': 10000.0}])
    result = select_final_capacity_output(tnv, _cfg(stability_max_decrease_pct=0.40), previous_capacity_df=prev)
    assert result.iloc[0]['selected_action'] == 'DECLINE'
    assert result.iloc[0]['CreditLimit'] == 0


def test_restrict_zeroed_after_stability_smoothing():
    """RESTRICT must produce CreditLimit=0 even when prior limit would clip it up."""
    tnv = _make_tnv_df('A', 'RESTRICT', expected_tnv=10.0, target_capacity_raw=0.0)
    prev = pd.DataFrame([{'subscriber_msisdn': 'A', 'CreditLimit': 15000.0}])
    result = select_final_capacity_output(tnv, _cfg(stability_max_decrease_pct=0.40), previous_capacity_df=prev)
    assert result.iloc[0]['CreditLimit'] == 0


def test_all_output_keys_present(engine_outputs):
    assert 'decision_base_df' in engine_outputs
    assert 'vw7_credit_v1_policy_prefilter' in engine_outputs
    assert 'vw8_credit_v1_tnv_action_evaluation' in engine_outputs
    assert 'vw9_credit_v1_final_capacity_output' in engine_outputs


# ---------------------------------------------------------------------------
# Multi-reason tracking (all_triggered_reasons / triggered_reason_count)
# ---------------------------------------------------------------------------

def test_all_triggered_reasons_multiple_flags():
    """Subscriber with both high fraud AND severe DSI should have both in all_triggered_reasons."""
    base = _base(
        l0_overrides={
            'fraud_abuse_risk_score_v1_rule': 0.90,
            'debt_stress_index_v1': 90.0,
        },
    )
    pf = build_policy_prefilter(base, _cfg())
    reasons = pf.iloc[0]['all_triggered_reasons']
    assert 'HIGH_FRAUD_ABUSE_RISK' in reasons
    assert 'SEVERE_DSI' in reasons
    assert reasons.count(';') >= 1
    assert pf.iloc[0]['triggered_reason_count'] >= 2


def test_all_triggered_reasons_single_flag():
    """Only one reason fires — all_triggered_reasons should be that single reason."""
    base = _base(l0_overrides={'fraud_abuse_risk_score_v1_rule': 0.90})
    pf = build_policy_prefilter(base, _cfg())
    reasons = pf.iloc[0]['all_triggered_reasons']
    assert reasons == 'HIGH_FRAUD_ABUSE_RISK'
    assert pf.iloc[0]['triggered_reason_count'] == 1


def test_all_triggered_reasons_pass():
    """Healthy subscriber — all_triggered_reasons should be PASS."""
    base = _base()
    pf = build_policy_prefilter(base, _cfg())
    assert pf.iloc[0]['all_triggered_reasons'] == 'PASS'
    assert pf.iloc[0]['triggered_reason_count'] == 0


# ---------------------------------------------------------------------------
# Final-capacity audit columns (binding_cap / after_vw6_cap)
# ---------------------------------------------------------------------------

def test_binding_cap_tnv_negative():
    """DECLINE subscriber shows TNV_NEGATIVE."""
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=-50.0)
    result = select_final_capacity_output(tnv, _cfg())
    assert result.iloc[0]['binding_cap'] == 'TNV_NEGATIVE'


def test_binding_cap_policy_cap():
    """Raw capacity > policy cap → binding_cap = POLICY_CAP."""
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0, target_capacity_raw=30000.0)
    result = select_final_capacity_output(tnv, _cfg(policy_capacity_cap=20000.0))
    assert result.iloc[0]['binding_cap'] == 'POLICY_CAP'


def test_binding_cap_vw6():
    """vw6 cap binds below policy cap → binding_cap = VW6_CAP."""
    tnv = _make_tnv_df('A', 'INCREASE_MEDIUM', expected_tnv=3000.0, target_capacity_raw=18000.0)
    vw6 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': 'A',
        'conservative_credit_limit_v1': 5000.0,
        'recommended_action': 'MAINTAIN',
    }])
    result = select_final_capacity_output(tnv, _cfg(), vw6_cap_action_df=vw6)
    assert result.iloc[0]['binding_cap'] == 'VW6_CAP'


def test_binding_cap_unconstrained():
    """No cap binds — binding_cap = UNCONSTRAINED."""
    tnv = _make_tnv_df('A', 'MAINTAIN', expected_tnv=100.0, target_capacity_raw=5000.0)
    result = select_final_capacity_output(tnv, _cfg(policy_capacity_cap=20000.0))
    assert result.iloc[0]['binding_cap'] == 'UNCONSTRAINED'
