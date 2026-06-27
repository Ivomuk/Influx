# Data Contracts — authoritative schema definitions for every SQL→Python
# and Python→Python interface in the credit V1 pipeline.
#
# validate_dataframe_contract(df, contract_name) raises ContractViolationError
# on missing required columns or unexpected nulls in non-nullable columns.
#
# validate_all_pipeline_inputs() is called by the orchestrator immediately
# after the SQL batch pipeline completes, before prepare_decision_base.
# This catches schema drift at the boundary rather than producing silent
# wrong answers inside the scoring logic.

import pandas as pd
import numpy as np


class ContractViolationError(Exception):
    pass


# ---------------------------------------------------------------------------
# Schema definitions
# { column: { 'required': bool, 'nullable': bool } }
# dtype coercion is the responsibility of the producing view, not enforced here.
# ---------------------------------------------------------------------------

LAYER1_FEATURES_CONTRACT = {
    'feature_dt':                         {'required': True,  'nullable': False},
    'subscriber_msisdn':                  {'required': True,  'nullable': False},
    # Exposure
    'outstanding_exposure_amt':           {'required': True,  'nullable': False},
    'active_lender_cnt_30d':              {'required': True,  'nullable': False, 'min': 0},
    'disbursement_cnt_30d':               {'required': True,  'nullable': False, 'min': 0},
    'days_since_last_disbursement':       {'required': True,  'nullable': True,  'min': 0},
    # Repayment
    'repayment_ratio_30d':                {'required': True,  'nullable': True,  'min': 0},
    'repayment_ratio_trend_7d':           {'required': True,  'nullable': True},
    # Wallet
    'wallet_inflow_amt_30d':              {'required': True,  'nullable': False, 'min': 0},
    'wallet_outflow_amt_30d':             {'required': True,  'nullable': False, 'min': 0},
    'spend_amt_30d':                      {'required': True,  'nullable': False, 'min': 0},
    'wallet_active_days_30d':             {'required': True,  'nullable': False, 'min': 0, 'max': 30},
    'wallet_inflow_trend_7d_vs_90d':      {'required': True,  'nullable': True},
    # Anti-gaming flags
    'stacked_borrowing_flag_30d':         {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'repeated_borrowing_flag_30d':        {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'timing_manipulation_flag':           {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'suspicious_repayment_jump_flag':     {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'loan_cycling_flag':                  {'required': True,  'nullable': False, 'min': 0, 'max': 1},
}

LAYER0_SCORES_CONTRACT = {
    'feature_dt':                                          {'required': True,  'nullable': False},
    'subscriber_msisdn':                                   {'required': True,  'nullable': False},
    'expected_repayment_probability_v1_rule':              {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'expected_credit_loss_rate_v1_rule':                   {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'churn_cooling_probability_v1_rule':                   {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'expected_future_transaction_margin_score_v1_rule':    {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'expected_treatment_cost_score_v1_rule':               {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'customer_lifetime_value_contribution_v1_rule':        {'required': True,  'nullable': False},
    'debt_stress_index_v1':                                {'required': True,  'nullable': False, 'min': 0, 'max': 100},
    'fraud_abuse_risk_score_v1_rule':                      {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'behavior_consistency_score_v1_rule':                  {'required': True,  'nullable': False, 'min': 0, 'max': 1},
    'identity_confidence_score_v1_rule':                   {'required': True,  'nullable': False, 'min': 0, 'max': 1},
}

VW5_REASON_CODES_CONTRACT = {
    'feature_dt':          {'required': True, 'nullable': False},
    'subscriber_msisdn':   {'required': True, 'nullable': False},
    'primary_reason_code': {'required': True, 'nullable': False},
}

VW6_CAP_ACTION_CONTRACT = {
    'feature_dt':                    {'required': True, 'nullable': False},
    'subscriber_msisdn':             {'required': True, 'nullable': False},
    'conservative_credit_limit_v1':  {'required': True, 'nullable': False},
    'recommended_action':            {'required': True, 'nullable': False},
}

FINAL_CAPACITY_CONTRACT = {
    'feature_dt':            {'required': True, 'nullable': False},
    'subscriber_msisdn':     {'required': True, 'nullable': False},
    'CreditLimit':           {'required': True, 'nullable': False, 'min': 0},
    'selected_action':       {'required': True, 'nullable': False},
    'decision_status':       {'required': True, 'nullable': False},
    'binding_cap':           {'required': True, 'nullable': False},
    'config_hash':           {'required': True, 'nullable': False},
    'policy_version':        {'required': True, 'nullable': False},
    'all_triggered_reasons': {'required': True, 'nullable': False},
    'after_vw6_cap':         {'required': True, 'nullable': False, 'min': 0},
}

CONTRACTS = {
    'layer1_features':  LAYER1_FEATURES_CONTRACT,
    'layer0_scores':    LAYER0_SCORES_CONTRACT,
    'vw5_reason_codes': VW5_REASON_CODES_CONTRACT,
    'vw6_cap_action':   VW6_CAP_ACTION_CONTRACT,
    'final_capacity':   FINAL_CAPACITY_CONTRACT,
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_dataframe_contract(df, contract_name, strict=True):
    """
    Validates df against the named contract.
    strict=True  → raises ContractViolationError on any required-column violation.
    strict=False → collects all violations and returns them without raising.
    Returns: list of violation strings (empty = full pass).
    """
    contract = CONTRACTS.get(contract_name)
    if contract is None:
        raise ValueError(f'Unknown contract: {contract_name}. Known: {list(CONTRACTS.keys())}')

    violations = []
    warnings = []

    for col, spec in contract.items():
        if col not in df.columns:
            if spec['required']:
                violations.append(f'MISSING_REQUIRED_COLUMN: {col}')
            else:
                warnings.append(f'MISSING_OPTIONAL_COLUMN: {col}')
            continue

        if not spec['nullable'] and df[col].isna().any():
            null_count = int(df[col].isna().sum())
            violations.append(f'UNEXPECTED_NULLS: {col} ({null_count} null values)')

        if 'min' in spec and pd.api.types.is_numeric_dtype(df[col]):
            below = df[col].dropna().lt(spec['min'])
            if below.any():
                violations.append(f'RANGE_VIOLATION: {col} has {int(below.sum())} values below {spec["min"]}')

        if 'max' in spec and pd.api.types.is_numeric_dtype(df[col]):
            above = df[col].dropna().gt(spec['max'])
            if above.any():
                violations.append(f'RANGE_VIOLATION: {col} has {int(above.sum())} values above {spec["max"]}')

    for w in warnings:
        print(f'CONTRACT_WARNING [{contract_name}]: {w}')

    if violations:
        msg = f'Contract violations [{contract_name}]: {violations}'
        if strict:
            raise ContractViolationError(msg)
        for v in violations:
            print(f'CONTRACT_VIOLATION [{contract_name}]: {v}')

    return violations


def validate_all_pipeline_inputs(layer1_df, layer0_df, vw5_df=None, vw6_df=None, strict=True):
    """
    Validates all DataFrames entering the Python decision engine.
    Called by the orchestrator before run_credit_v1_decision_engine.
    Returns a dict of {contract_name: [violations]}.
    strict=True halts the pipeline on the first missing required column.
    """
    results = {}
    results['layer1_features'] = validate_dataframe_contract(layer1_df, 'layer1_features', strict=strict)
    results['layer0_scores']   = validate_dataframe_contract(layer0_df,  'layer0_scores',   strict=strict)
    if vw5_df is not None:
        results['vw5_reason_codes'] = validate_dataframe_contract(vw5_df, 'vw5_reason_codes', strict=strict)
    if vw6_df is not None:
        results['vw6_cap_action']   = validate_dataframe_contract(vw6_df, 'vw6_cap_action',   strict=strict)

    total_violations = sum(len(v) for v in results.values())
    if total_violations == 0:
        print(f'Data contracts: all {len(results)} contracts passed.')
    else:
        print(f'Data contracts: {total_violations} violations across {len(results)} contracts.')
    return results
