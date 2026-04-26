"""
End-to-end smoke tests: layer1 + layer0 → engine → QA → contracts → lender interface.
Each test traverses multiple modules to verify that the full pipeline wires correctly.
"""
import pytest
import pandas as pd
import numpy as np

from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import (
    run_credit_v1_decision_engine,
    ACTION_SPECS_DF,
)
from telecom_credit_engine.contracts.data_contracts import (
    validate_dataframe_contract,
    validate_all_pipeline_inputs,
    ContractViolationError,
)
from telecom_credit_engine.interfaces.external_lender_interface import build_external_lender_interface
from telecom_credit_engine.governance.operating_modes import (
    detect_operating_mode,
    apply_operating_mode_overrides,
    OPERATING_MODE_CONSERVATIVE,
    OPERATING_MODE_NORMAL,
)
from telecom_credit_engine.monitoring.qa_decision_engine_output import run_qa_decision_engine_output


# ---------------------------------------------------------------------------
# Full pipeline: layer1 → engine → QA → external lender interface
# ---------------------------------------------------------------------------

def test_full_pipeline_no_qa_failures(layer1_df, layer0_df, default_config, action_specs_df):
    validate_all_pipeline_inputs(layer1_df, layer0_df, strict=True)

    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )
    results = run_qa_decision_engine_output(outputs, default_config)
    failed = {k: v for k, v in results.items() if not v.empty}
    assert failed == {}, f'QA failures: {list(failed.keys())}'


def test_full_pipeline_external_lender_interface(layer1_df, layer0_df, default_config, action_specs_df):
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    lender_df = build_external_lender_interface(
        vw9, include_optional_fields=True, decision_timestamp='2026-04-01'
    )
    assert len(lender_df) == len(layer1_df)
    assert 'MSISDN' in lender_df.columns
    assert 'CreditLimit' in lender_df.columns
    assert (lender_df['CreditLimit'] >= 0).all()


def test_data_contracts_validate_engine_output(layer1_df, layer0_df, default_config, action_specs_df):
    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )
    vw9 = outputs['vw9_credit_v1_final_capacity_output']
    violations = validate_dataframe_contract(vw9, 'final_capacity', strict=True)
    assert violations == []


def test_contract_violation_stops_pipeline(layer0_df):
    # Drop a required column from layer1; strict=True should raise before engine runs
    bad_layer1 = pd.DataFrame([{
        'feature_dt': pd.Timestamp('2026-04-01'),
        'subscriber_msisdn': '256700000001',
        # wallet_inflow_amt_30d intentionally omitted
    }])
    with pytest.raises(ContractViolationError):
        validate_all_pipeline_inputs(bad_layer1, layer0_df, strict=True)


# ---------------------------------------------------------------------------
# Operating modes: detection → override → engine
# ---------------------------------------------------------------------------

def test_operating_mode_normal_with_clean_signals():
    mode, reasons = detect_operating_mode()
    assert mode == OPERATING_MODE_NORMAL


def test_operating_mode_conservative_reduces_credit_limit(layer1_df, layer0_df, default_config, action_specs_df):
    normal_outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )
    normal_limit = normal_outputs['vw9_credit_v1_final_capacity_output'].iloc[0]['CreditLimit']

    conservative_config = apply_operating_mode_overrides(
        default_config, OPERATING_MODE_CONSERVATIVE, circuit_breaker_multiplier=1.0
    )
    conservative_outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=conservative_config,
        action_specs_input_df=action_specs_df,
    )
    conservative_limit = conservative_outputs['vw9_credit_v1_final_capacity_output'].iloc[0]['CreditLimit']

    assert conservative_limit <= normal_limit
