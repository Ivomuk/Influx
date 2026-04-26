# QA suite for run_credit_v1_decision_engine outputs.
# Validates vw9_credit_v1_final_capacity_output (final_capacity_df) and
# vw7_credit_v1_policy_prefilter (policy_prefilter_df) after each engine run.
# Call run_qa_decision_engine_output(outputs, config) after run_credit_v1_decision_engine().
# Returns a dict of {check_name: fail_df}; all fail_dfs empty = full pass.

import pandas as pd
import numpy as np

VALID_ACTIONS = {'DECLINE', 'RESTRICT', 'REDUCE', 'MAINTAIN', 'INCREASE_SMALL', 'INCREASE_MEDIUM'}
# Three-value status mirrors the action distinction partners need:
#   declined            → DECLINE action (no prior credit or TNV ≤ 0); do not disburse
#   blocked_or_restricted → RESTRICT action (prior credit, now blocked); manage existing exposure
#   approved_or_maintained → all other actions; CreditLimit > 0
VALID_DECISION_STATUSES = {'approved_or_maintained', 'blocked_or_restricted', 'declined'}
VALID_REASON_CODES = {
    'LOW_IDENTITY_CONFIDENCE', 'HIGH_FRAUD_ABUSE_RISK', 'SEVERE_DSI',
    'RECENT_DISBURSEMENT_NO_REPAYMENT', 'TIMING_MANIPULATION_DETECTED',
    'LOAN_CYCLING_DETECTED', 'STACKED_BORROWING', 'LOW_REPAYMENT_RATIO',
    'HIGH_ACTIVE_LENDER_COUNT', 'HIGH_EXPOSURE_TO_INFLOW',
    'THIN_FILE_CONSERVATIVE_PATH', 'PASS',
    # vw5 reason codes
    'VERY_LOW_REPAYMENT_RATIO', 'REPEATED_BORROWING', 'HIGH_COOLING_RISK',
    'ELIGIBLE_BASELINE',
}


def _fail(df, label):
    if not df.empty:
        print(f'QA FAIL [{label}]: {len(df)} rows')
    return df


def qa_final_capacity(final_capacity_df, config_dict, previous_capacity_df=None):
    """Runs all checks on vw9_credit_v1_final_capacity_output."""
    results = {}
    df = final_capacity_df.copy()
    policy_cap = config_dict.get('policy_capacity_cap', float('inf'))

    # QA-DE-01: CreditLimit is negative
    results['QA-DE-01_negative_credit_limit'] = _fail(
        df[df['CreditLimit'] < 0][['feature_dt', 'subscriber_msisdn', 'CreditLimit']],
        'QA-DE-01'
    )

    # QA-DE-02: CreditLimit exceeds policy_capacity_cap
    results['QA-DE-02_exceeds_policy_cap'] = _fail(
        df[df['CreditLimit'] > policy_cap + 1][['feature_dt', 'subscriber_msisdn', 'CreditLimit']],
        'QA-DE-02'
    )

    # QA-DE-03: Invalid selected_action
    results['QA-DE-03_invalid_action'] = _fail(
        df[~df['selected_action'].isin(VALID_ACTIONS)][['feature_dt', 'subscriber_msisdn', 'selected_action']],
        'QA-DE-03'
    )

    # QA-DE-04: Invalid decision_status
    results['QA-DE-04_invalid_decision_status'] = _fail(
        df[~df['decision_status'].isin(VALID_DECISION_STATUSES)][['feature_dt', 'subscriber_msisdn', 'decision_status']],
        'QA-DE-04'
    )

    # QA-DE-05: Duplicate subscriber_msisdn per feature_dt
    dupes = (
        df.groupby(['feature_dt', 'subscriber_msisdn'])
        .size()
        .reset_index(name='count')
    )
    results['QA-DE-05_duplicate_msisdn'] = _fail(
        dupes[dupes['count'] > 1],
        'QA-DE-05'
    )

    # QA-DE-06: DECLINE/RESTRICT action but CreditLimit > 0
    restricted = df[df['selected_action'].isin(['DECLINE', 'RESTRICT'])]
    results['QA-DE-06_restricted_with_nonzero_limit'] = _fail(
        restricted[restricted['CreditLimit'] > 0][['feature_dt', 'subscriber_msisdn', 'selected_action', 'CreditLimit']],
        'QA-DE-06'
    )

    # QA-DE-07: Stability bounds violated (requires previous_capacity_df)
    if previous_capacity_df is not None and not previous_capacity_df.empty:
        max_inc = config_dict.get('stability_max_increase_pct', 0.25)
        max_dec = config_dict.get('stability_max_decrease_pct', 0.40)
        merged = df.merge(
            previous_capacity_df[['subscriber_msisdn', 'CreditLimit']].rename(
                columns={'CreditLimit': 'prev_limit'}),
            on='subscriber_msisdn', how='inner'
        )
        has_prior = merged['prev_limit'] > 0
        upper = merged['prev_limit'] * (1 + max_inc)
        lower = (merged['prev_limit'] * (1 - max_dec)).clip(lower=0)
        violated = merged[
            has_prior
            & ((merged['CreditLimit'] > upper + 1) | (merged['CreditLimit'] < lower - 1))
        ]
        results['QA-DE-07_stability_bounds_violated'] = _fail(
            violated[['subscriber_msisdn', 'CreditLimit', 'prev_limit']],
            'QA-DE-07'
        )

    return results


def qa_policy_prefilter(policy_prefilter_df):
    """Runs checks on vw7_credit_v1_policy_prefilter."""
    results = {}
    df = policy_prefilter_df.copy()

    # QA-DE-08: Invalid primary_policy_reason
    results['QA-DE-08_invalid_reason_code'] = _fail(
        df[~df['primary_policy_reason'].isin(VALID_REASON_CODES)][['feature_dt', 'subscriber_msisdn', 'primary_policy_reason']],
        'QA-DE-08'
    )

    # QA-DE-09: policy_restrict_flag outside {0, 1}
    results['QA-DE-09_invalid_restrict_flag'] = _fail(
        df[~df['policy_restrict_flag'].isin([0, 1])][['feature_dt', 'subscriber_msisdn', 'policy_restrict_flag']],
        'QA-DE-09'
    )

    # QA-DE-10: Feasible action set is empty (every action blocked)
    results['QA-DE-10_empty_feasible_set'] = _fail(
        df[df['feasible_action_set'].str.strip() == ''][['feature_dt', 'subscriber_msisdn', 'primary_policy_reason']],
        'QA-DE-10'
    )

    return results


def run_qa_decision_engine_output(engine_outputs, config_dict, previous_capacity_df=None):
    """
    Entry point. Pass the dict returned by run_credit_v1_decision_engine().
    Returns combined results dict; prints a summary line per check.
    """
    final_df = engine_outputs['vw9_credit_v1_final_capacity_output']
    prefilter_df = engine_outputs['vw7_credit_v1_policy_prefilter']

    results = {}
    results.update(qa_final_capacity(final_df, config_dict, previous_capacity_df))
    results.update(qa_policy_prefilter(prefilter_df))

    failed = {k: v for k, v in results.items() if not v.empty}
    passed = len(results) - len(failed)
    print(f'\nDecision Engine QA: {passed}/{len(results)} checks passed.')
    if failed:
        print(f'FAILED checks: {list(failed.keys())}')
    return results
