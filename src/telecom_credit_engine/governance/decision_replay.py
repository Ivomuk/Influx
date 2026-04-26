# Decision Replay — re-runs the decision engine on immutable snapshots
# and diffs the output against the original decisions.
#
# store_decision_snapshot (dim6_outcome_tracking) already captures the full
# set of layer1 + layer0 inputs alongside each decision. This module adds:
#
#   replay_decisions(snapshot_df)      — re-runs engine, returns new decisions
#   diff_decisions(original, replayed) — compares CreditLimit, action, reason code
#   detect_nondeterminism(snapshot_df) — runs N times, flags any output variance
#
# Use cases:
#   Audit a disputed credit limit:
#     replay_decisions(snapshot) under same config → identical result confirms
#     the system behaved correctly at that point in time.
#   Config change impact analysis:
#     replay_decisions(snapshot, config_dict=new_config) → diff shows exactly
#     which subscribers are affected and by how much.
#   Regression check before deployment:
#     diff_decisions(baseline_df, replay_df) must be empty for a deterministic
#     change; non-empty diff pinpoints unintended side effects.

import pandas as pd
import numpy as np
from datetime import datetime, timezone

_LAYER1_COLS = [
    'feature_dt', 'subscriber_msisdn', 'outstanding_exposure_amt',
    'active_lender_cnt_30d', 'disbursement_cnt_30d', 'days_since_last_disbursement',
    'repayment_ratio_30d', 'wallet_inflow_amt_30d', 'wallet_outflow_amt_30d',
    'spend_amt_30d', 'wallet_active_days_30d',
    'stacked_borrowing_flag_30d', 'repeated_borrowing_flag_30d',
    'timing_manipulation_flag', 'suspicious_repayment_jump_flag', 'loan_cycling_flag',
    'repayment_ratio_trend_7d', 'wallet_inflow_trend_7d_vs_90d',
]

_LAYER0_COLS = [
    'feature_dt', 'subscriber_msisdn',
    'expected_repayment_probability_v1_rule', 'expected_credit_loss_rate_v1_rule',
    'churn_cooling_probability_v1_rule', 'expected_future_transaction_margin_score_v1_rule',
    'expected_treatment_cost_score_v1_rule', 'customer_lifetime_value_contribution_v1_rule',
    'debt_stress_index_v1', 'fraud_abuse_risk_score_v1_rule',
    'behavior_consistency_score_v1_rule', 'identity_confidence_score_v1_rule',
]

_DIFF_FIELDS = ['CreditLimit', 'selected_action', 'decision_status', 'primary_policy_reason']


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def replay_decisions(
    snapshot_df,
    config_dict=None,
    vw5_reason_codes_df=None,
    vw6_cap_action_df=None,
    circuit_breaker_multiplier=1.0,
    previous_capacity_df=None,
):
    """
    Re-runs the decision engine on a snapshot DataFrame produced by
    store_decision_snapshot. The snapshot must contain all layer1 + layer0
    columns (extra columns are ignored).

    Returns vw9_credit_v1_final_capacity_output with an added replay_dt column.
    Passing a different config_dict enables counterfactual / what-if analysis.
    """
    from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import (
        run_credit_v1_decision_engine, DECISION_ENGINE_CONFIG, ACTION_SPECS_DF
    )

    layer1_df = snapshot_df[[c for c in _LAYER1_COLS if c in snapshot_df.columns]].copy()
    layer0_df = snapshot_df[[c for c in _LAYER0_COLS if c in snapshot_df.columns]].copy()

    local_config = DECISION_ENGINE_CONFIG.copy() if config_dict is None else config_dict.copy()

    outputs = run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=local_config,
        action_specs_input_df=ACTION_SPECS_DF,
        previous_capacity_df=previous_capacity_df,
        vw5_reason_codes_df=vw5_reason_codes_df,
        vw6_cap_action_df=vw6_cap_action_df,
        circuit_breaker_multiplier=circuit_breaker_multiplier,
    )
    replayed_df = outputs['vw9_credit_v1_final_capacity_output'].copy()
    replayed_df['replay_dt'] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return replayed_df


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def diff_decisions(original_df, replayed_df, tolerance_amt=1.0):
    """
    Compares two decision DataFrames for the same (feature_dt, subscriber_msisdn) keys.

    tolerance_amt: CreditLimit differences ≤ this are not flagged
                   (absorbs floating-point rounding differences).

    Returns a DataFrame of rows where at least one field changed, with columns:
      subscriber_msisdn, feature_dt,
      original_CreditLimit, replayed_CreditLimit, delta_CreditLimit,
      original_action, replayed_action,
      original_status, replayed_status,
      original_reason, replayed_reason,
      changed_fields   (comma-separated list of changed field names)

    An empty DataFrame means the two runs are identical within tolerance.
    """
    key_cols   = ['feature_dt', 'subscriber_msisdn']
    value_cols = [c for c in _DIFF_FIELDS if c in original_df.columns or c in replayed_df.columns]

    orig = original_df[key_cols + [c for c in value_cols if c in original_df.columns]].copy()
    repl = replayed_df[key_cols + [c for c in value_cols if c in replayed_df.columns]].copy()

    merged = orig.merge(repl, on=key_cols, suffixes=('_orig', '_repl'), how='outer', indicator=True)

    rows = []
    for _, row in merged.iterrows():
        changed = []

        orig_cl = row.get('CreditLimit_orig', np.nan)
        repl_cl = row.get('CreditLimit_repl', np.nan)
        delta   = abs(orig_cl - repl_cl) if pd.notna(orig_cl) and pd.notna(repl_cl) else np.nan
        if pd.isna(delta) or delta > tolerance_amt:
            changed.append('CreditLimit')

        for field in ['selected_action', 'decision_status', 'primary_policy_reason']:
            orig_val = row.get(f'{field}_orig')
            repl_val = row.get(f'{field}_repl')
            if orig_val != repl_val:
                changed.append(field)

        if changed or row['_merge'] != 'both':
            rows.append({
                'subscriber_msisdn':    row.get('subscriber_msisdn'),
                'feature_dt':           row.get('feature_dt'),
                'original_CreditLimit': orig_cl,
                'replayed_CreditLimit': repl_cl,
                'delta_CreditLimit':    round(delta, 2) if pd.notna(delta) else None,
                'original_action':      row.get('selected_action_orig'),
                'replayed_action':      row.get('selected_action_repl'),
                'original_status':      row.get('decision_status_orig'),
                'replayed_status':      row.get('decision_status_repl'),
                'original_reason':      row.get('primary_policy_reason_orig'),
                'replayed_reason':      row.get('primary_policy_reason_repl'),
                'changed_fields':       ','.join(changed) if changed else 'MISSING_IN_ONE_SET',
            })

    diff_df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=[
        'subscriber_msisdn', 'feature_dt',
        'original_CreditLimit', 'replayed_CreditLimit', 'delta_CreditLimit',
        'original_action', 'replayed_action',
        'original_status', 'replayed_status',
        'original_reason', 'replayed_reason',
        'changed_fields',
    ])

    matched = int((merged['_merge'] == 'both').sum())
    print(f'Decision diff: {len(diff_df)} of {matched} matched subscribers changed '
          f'(tolerance={tolerance_amt}).')
    return diff_df


# ---------------------------------------------------------------------------
# Non-determinism detection
# ---------------------------------------------------------------------------

def detect_nondeterminism(snapshot_df, config_dict=None, n_runs=3, tolerance_amt=1.0):
    """
    Runs replay_decisions n_runs times on the same snapshot and verifies that
    every run produces identical output. A correct deterministic engine must
    return the same CreditLimit, action, and status for the same inputs on
    every invocation.

    Returns:
      {
        'deterministic': bool,
        'variance_df':   DataFrame of differing rows across runs (empty if deterministic),
        'n_runs':        int,
      }
    """
    runs = [replay_decisions(snapshot_df, config_dict=config_dict) for _ in range(n_runs)]

    all_diffs = []
    for i, run in enumerate(runs[1:], start=1):
        diff = diff_decisions(runs[0], run, tolerance_amt=tolerance_amt)
        if not diff.empty:
            diff['vs_run_index'] = i
            all_diffs.append(diff)

    variance_df   = pd.concat(all_diffs, ignore_index=True) if all_diffs else pd.DataFrame()
    deterministic = variance_df.empty

    print(f'Non-determinism check ({n_runs} runs): '
          f'{"DETERMINISTIC" if deterministic else f"NON-DETERMINISTIC — {len(variance_df)} differing rows"}')
    return {'deterministic': deterministic, 'variance_df': variance_df, 'n_runs': n_runs}
