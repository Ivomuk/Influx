# Dimension 4 upgrade of the Layer 2–4 decision engine.
# Adds:
#   - Anti-gaming policy blocks (timing manipulation, loan cycling)
#   - Decision stability smoothing (bounded capacity change between cycles)
#   - Network-wide lending budget constraint
#   - previous_capacity_df parameter for stability context
# All existing function signatures are backward-compatible.

import pandas as pd
import numpy as np
from tqdm import tqdm

tqdm.pandas()

# -----------------------------------------------------------------------
# Configuration (loaded from Config.txt in production)
# -----------------------------------------------------------------------
DECISION_ENGINE_CONFIG = {
    'identity_confidence_restrict_threshold': 0.70,
    'fraud_restrict_threshold': 0.80,
    'severe_dsi_threshold': 85.0,
    'reduce_only_dsi_min': 60.0,
    'reduce_only_dsi_max': 84.0,
    'maintain_only_dsi_min': 40.0,
    'maintain_only_dsi_max': 59.0,
    'recent_disb_days_threshold': 3,
    'recent_disb_repayment_threshold': 0.10,
    'low_repayment_ratio_threshold': 0.50,
    'high_exposure_to_inflow_threshold': 0.80,
    'high_active_lender_cnt_threshold': 3,
    'thin_file_wallet_days_threshold': 5,
    'thin_file_capacity_cap': 5000.0,
    'policy_capacity_cap': 20000.0,
    'validity_days_restrict': 7,
    'validity_days_reduce': 14,
    'validity_days_maintain': 14,
    'validity_days_increase': 7,
    'output_version': 'credit_v1_python_decision_engine_prod',
    'stability_max_increase_pct': 0.25,
    'stability_max_decrease_pct': 0.40,
    'stability_restrict_cooldown_days': 14,
    'budget_constraint_enabled': False,
    'network_lending_budget_total': 50_000_000.0,
    'budget_priority_states': ['Healthy', 'Recovered'],
    'block_timing_manipulation': True,
    'block_loan_cycling': True,
    'tnv_base_capacity_factor': 0.35,
    'tnv_future_margin_multiplier': 0.05,
    'tnv_churn_cost_multiplier': 0.03,
    'tnv_treatment_cost_scalar': 500.0,
}

ACTION_SPECS_DF = pd.DataFrame([
    {'action': 'DECLINE',         'capacity_multiplier': 0.00, 'base_margin_rate': 0.00, 'churn_relief': 0.02, 'treatment_multiplier': 0.20, 'stability_multiplier': 0.00, 'update_direction': 'restrict',  'validity_days_key': 'validity_days_restrict'},
    {'action': 'RESTRICT',        'capacity_multiplier': 0.00, 'base_margin_rate': 0.00, 'churn_relief': 0.05, 'treatment_multiplier': 0.30, 'stability_multiplier': 0.00, 'update_direction': 'restrict',  'validity_days_key': 'validity_days_restrict'},
    {'action': 'REDUCE',          'capacity_multiplier': 0.50, 'base_margin_rate': 0.08, 'churn_relief': 0.01, 'treatment_multiplier': 0.60, 'stability_multiplier': 0.90, 'update_direction': 'reduce',    'validity_days_key': 'validity_days_reduce'},
    {'action': 'MAINTAIN',        'capacity_multiplier': 1.00, 'base_margin_rate': 0.10, 'churn_relief': 0.00, 'treatment_multiplier': 1.00, 'stability_multiplier': 1.00, 'update_direction': 'maintain',  'validity_days_key': 'validity_days_maintain'},
    {'action': 'INCREASE_SMALL',  'capacity_multiplier': 1.25, 'base_margin_rate': 0.11, 'churn_relief': -0.01, 'treatment_multiplier': 1.10, 'stability_multiplier': 0.95, 'update_direction': 'increase', 'validity_days_key': 'validity_days_increase'},
    {'action': 'INCREASE_MEDIUM', 'capacity_multiplier': 1.50, 'base_margin_rate': 0.12, 'churn_relief': -0.02, 'treatment_multiplier': 1.25, 'stability_multiplier': 0.90, 'update_direction': 'increase', 'validity_days_key': 'validity_days_increase'},
])

REQUIRED_LAYER1_COLS = [
    'feature_dt', 'subscriber_msisdn', 'outstanding_exposure_amt', 'active_lender_cnt_30d',
    'disbursement_cnt_30d', 'days_since_last_disbursement', 'repayment_ratio_30d',
    'wallet_inflow_amt_30d', 'wallet_outflow_amt_30d', 'spend_amt_30d', 'wallet_active_days_30d',
    'stacked_borrowing_flag_30d', 'repeated_borrowing_flag_30d'
]
REQUIRED_LAYER0_COLS = [
    'feature_dt', 'subscriber_msisdn', 'expected_repayment_probability_v1_rule',
    'expected_credit_loss_rate_v1_rule', 'churn_cooling_probability_v1_rule',
    'expected_future_transaction_margin_score_v1_rule', 'expected_treatment_cost_score_v1_rule',
    'customer_lifetime_value_contribution_v1_rule', 'debt_stress_index_v1',
    'fraud_abuse_risk_score_v1_rule', 'behavior_consistency_score_v1_rule',
    'identity_confidence_score_v1_rule'
]

# -----------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------

def validate_required_columns(input_df, required_cols, df_name):
    missing_cols = [col for col in required_cols if col not in input_df.columns]
    if missing_cols:
        raise ValueError(df_name + ' is missing required columns: ' + ', '.join(missing_cols))

def coerce_numeric_columns(input_df, numeric_cols):
    output_df = input_df.copy()
    for col in numeric_cols:
        output_df[col] = pd.to_numeric(output_df[col], errors='coerce')
    return output_df

# -----------------------------------------------------------------------
# Stage 0: Merge and clean inputs
# -----------------------------------------------------------------------

def prepare_decision_base(layer1_input_df, layer0_input_df):
    validate_required_columns(layer1_input_df, REQUIRED_LAYER1_COLS, 'layer1_input_df')
    validate_required_columns(layer0_input_df, REQUIRED_LAYER0_COLS, 'layer0_input_df')
    layer1_clean_df = layer1_input_df.copy()
    layer0_clean_df = layer0_input_df.copy()
    layer1_clean_df['feature_dt'] = pd.to_datetime(layer1_clean_df['feature_dt'])
    layer0_clean_df['feature_dt'] = pd.to_datetime(layer0_clean_df['feature_dt'])
    layer1_numeric_cols = [c for c in REQUIRED_LAYER1_COLS if c not in ('feature_dt', 'subscriber_msisdn')]
    layer0_numeric_cols = [c for c in REQUIRED_LAYER0_COLS if c not in ('feature_dt', 'subscriber_msisdn')]
    layer1_clean_df = coerce_numeric_columns(layer1_clean_df, layer1_numeric_cols)
    layer0_clean_df = coerce_numeric_columns(layer0_clean_df, layer0_numeric_cols)
    merged_df = layer1_clean_df.merge(layer0_clean_df, on=['feature_dt', 'subscriber_msisdn'], how='inner', validate='one_to_one')
    if merged_df.empty:
        raise ValueError('Merged decision base is empty after joining layer1_input_df and layer0_input_df')
    return merged_df

# -----------------------------------------------------------------------
# Stage 1: Policy pre-filter (Layer 3)
# Dimension 4 additions: anti-gaming policy blocks.
# -----------------------------------------------------------------------

def build_policy_prefilter(decision_base_input_df, config_dict, vw5_reason_codes_df=None):
    output_df = decision_base_input_df.copy()

    output_df['exposure_to_inflow_ratio'] = np.where(
        output_df['wallet_inflow_amt_30d'] > 0,
        output_df['outstanding_exposure_amt'] / output_df['wallet_inflow_amt_30d'],
        np.where(output_df['outstanding_exposure_amt'] > 0, 999.0, 0.0)
    )
    output_df['thin_file_flag'] = (
        output_df['wallet_active_days_30d'] < config_dict['thin_file_wallet_days_threshold']
    ).astype(int)
    output_df['recent_disbursement_no_repayment_flag'] = (
        (output_df['days_since_last_disbursement'] <= config_dict['recent_disb_days_threshold'])
        & (output_df['repayment_ratio_30d'].fillna(0) < config_dict['recent_disb_repayment_threshold'])
    ).astype(int)

    # Anti-gaming blocks (Dimension 4) — read flags passed through from vw4.
    timing_block = (
        config_dict.get('block_timing_manipulation', True)
        and 'timing_manipulation_flag' in output_df.columns
    )
    cycling_block = (
        config_dict.get('block_loan_cycling', True)
        and 'loan_cycling_flag' in output_df.columns
    )
    antigaming_restrict = pd.Series(False, index=output_df.index)
    if timing_block:
        antigaming_restrict |= (output_df['timing_manipulation_flag'] == 1)
    if cycling_block:
        antigaming_restrict |= (output_df['loan_cycling_flag'] == 1)

    output_df['policy_restrict_flag'] = (
        (output_df['identity_confidence_score_v1_rule'] < config_dict['identity_confidence_restrict_threshold'])
        | (output_df['fraud_abuse_risk_score_v1_rule'] >= config_dict['fraud_restrict_threshold'])
        | (output_df['debt_stress_index_v1'] >= config_dict['severe_dsi_threshold'])
        | (output_df['recent_disbursement_no_repayment_flag'] == 1)
        | antigaming_restrict
    ).astype(int)

    output_df['policy_reduce_only_flag'] = (
        (output_df['debt_stress_index_v1'].between(config_dict['reduce_only_dsi_min'], config_dict['reduce_only_dsi_max']))
        | (output_df['repayment_ratio_30d'].fillna(1.0) < config_dict['low_repayment_ratio_threshold'])
        | (output_df['stacked_borrowing_flag_30d'] == 1)
        | (output_df['active_lender_cnt_30d'] >= config_dict['high_active_lender_cnt_threshold'])
        | (output_df['exposure_to_inflow_ratio'] > config_dict['high_exposure_to_inflow_threshold'])
    ).astype(int)

    output_df['policy_maintain_only_flag'] = (
        (output_df['policy_restrict_flag'] == 0)
        & (output_df['policy_reduce_only_flag'] == 0)
        & (
            output_df['debt_stress_index_v1'].between(config_dict['maintain_only_dsi_min'], config_dict['maintain_only_dsi_max'])
            | (output_df['active_lender_cnt_30d'] == 2)
        )
    ).astype(int)

    output_df['allow_decline'] = 1
    output_df['allow_restrict'] = 1
    output_df['allow_reduce'] = 1
    output_df['allow_maintain'] = (output_df['policy_restrict_flag'] == 0).astype(int)
    output_df['allow_increase_small'] = (
        (output_df['policy_restrict_flag'] == 0) & (output_df['policy_reduce_only_flag'] == 0)
    ).astype(int)
    output_df['allow_increase_medium'] = (
        (output_df['policy_restrict_flag'] == 0)
        & (output_df['policy_reduce_only_flag'] == 0)
        & (output_df['policy_maintain_only_flag'] == 0)
        & (output_df['thin_file_flag'] == 0)
    ).astype(int)

    conditions = [
        output_df['identity_confidence_score_v1_rule'] < config_dict['identity_confidence_restrict_threshold'],
        output_df['fraud_abuse_risk_score_v1_rule'] >= config_dict['fraud_restrict_threshold'],
        output_df['debt_stress_index_v1'] >= config_dict['severe_dsi_threshold'],
        output_df['recent_disbursement_no_repayment_flag'] == 1,
        antigaming_restrict & output_df.get('timing_manipulation_flag', pd.Series(0, index=output_df.index)).eq(1),
        antigaming_restrict & output_df.get('loan_cycling_flag', pd.Series(0, index=output_df.index)).eq(1),
        output_df['stacked_borrowing_flag_30d'] == 1,
        output_df['repayment_ratio_30d'].fillna(1.0) < config_dict['low_repayment_ratio_threshold'],
        output_df['active_lender_cnt_30d'] >= config_dict['high_active_lender_cnt_threshold'],
        output_df['exposure_to_inflow_ratio'] > config_dict['high_exposure_to_inflow_threshold'],
        output_df['thin_file_flag'] == 1,
    ]
    choices = [
        'LOW_IDENTITY_CONFIDENCE', 'HIGH_FRAUD_ABUSE_RISK', 'SEVERE_DSI',
        'RECENT_DISBURSEMENT_NO_REPAYMENT',
        'TIMING_MANIPULATION_DETECTED', 'LOAN_CYCLING_DETECTED',
        'STACKED_BORROWING', 'LOW_REPAYMENT_RATIO',
        'HIGH_ACTIVE_LENDER_COUNT', 'HIGH_EXPOSURE_TO_INFLOW',
        'THIN_FILE_CONSERVATIVE_PATH',
    ]
    output_df['primary_policy_reason'] = np.select(conditions, choices, default='PASS')

    action_cols = ['allow_decline', 'allow_restrict', 'allow_reduce', 'allow_maintain', 'allow_increase_small', 'allow_increase_medium']
    action_names = ['DECLINE', 'RESTRICT', 'REDUCE', 'MAINTAIN', 'INCREASE_SMALL', 'INCREASE_MEDIUM']
    output_df['feasible_action_set'] = output_df[action_cols].apply(
        lambda row: ','.join([action_names[i] for i, v in enumerate(row) if v == 1]), axis=1
    )

    # ------------------------------------------------------------------
    # Wire vw5: use SQL-authoritative primary_reason_code where available.
    # Overrides the Python np.select result to ensure governance
    # reproducibility between the SQL audit trail and Python execution.
    # ------------------------------------------------------------------
    if vw5_reason_codes_df is not None and not vw5_reason_codes_df.empty:
        vw5_clean = vw5_reason_codes_df[['feature_dt', 'subscriber_msisdn', 'primary_reason_code']].copy()
        vw5_clean['feature_dt'] = pd.to_datetime(vw5_clean['feature_dt'])
        output_df = output_df.merge(vw5_clean, on=['feature_dt', 'subscriber_msisdn'], how='left')
        has_vw5_reason = (
            output_df['primary_reason_code'].notna()
            & (output_df['primary_reason_code'] != 'ELIGIBLE_BASELINE')
        )
        output_df['primary_policy_reason'] = np.where(
            has_vw5_reason,
            output_df['primary_reason_code'],
            output_df['primary_policy_reason']
        )
        output_df = output_df.drop(columns=['primary_reason_code'])

    return output_df

# -----------------------------------------------------------------------
# Stage 2: TNV evaluation (Layer 2)
# -----------------------------------------------------------------------

def evaluate_tnv_actions(policy_prefilter_input_df, action_specs_input_df, config_dict):
    policy_df = policy_prefilter_input_df.copy()
    base_cap_factor = config_dict.get('tnv_base_capacity_factor', 0.35)
    margin_mult = config_dict.get('tnv_future_margin_multiplier', 0.05)
    churn_mult = config_dict.get('tnv_churn_cost_multiplier', 0.03)
    treatment_scalar = config_dict.get('tnv_treatment_cost_scalar', 500.0)
    action_rows = []
    for _, sub_row in tqdm(policy_df.iterrows(), total=len(policy_df)):
        base_capacity = max(
            sub_row['wallet_inflow_amt_30d'] * base_cap_factor * sub_row['expected_repayment_probability_v1_rule'],
            0.0
        )
        if sub_row['thin_file_flag'] == 1:
            base_capacity = min(base_capacity, config_dict['thin_file_capacity_cap'])
        for _, spec in action_specs_input_df.iterrows():
            allow_col = 'allow_' + spec['action'].lower()
            if sub_row.get(allow_col, 0) != 1:
                continue
            target_capacity_raw = base_capacity * spec['capacity_multiplier']
            immediate_lending_margin = target_capacity_raw * spec['base_margin_rate']
            discounted_future_transaction_margin = (
                sub_row['expected_future_transaction_margin_score_v1_rule']
                * sub_row['wallet_inflow_amt_30d'] * margin_mult * spec['capacity_multiplier']
            )
            expected_credit_loss = target_capacity_raw * sub_row['expected_credit_loss_rate_v1_rule']
            adjusted_churn_probability = max(
                sub_row['churn_cooling_probability_v1_rule'] - spec['churn_relief'], 0.0
            )
            expected_churn_cost = adjusted_churn_probability * sub_row['wallet_inflow_amt_30d'] * churn_mult
            expected_treatment_cost = sub_row['expected_treatment_cost_score_v1_rule'] * treatment_scalar * spec['treatment_multiplier']
            expected_tnv = (
                immediate_lending_margin + discounted_future_transaction_margin
                - expected_credit_loss - expected_churn_cost - expected_treatment_cost
            )
            action_rows.append({
                'feature_dt': sub_row['feature_dt'],
                'subscriber_msisdn': sub_row['subscriber_msisdn'],
                'primary_policy_reason': sub_row['primary_policy_reason'],
                'action': spec['action'],
                'target_capacity_raw': target_capacity_raw,
                'immediate_lending_margin': immediate_lending_margin,
                'discounted_future_transaction_margin': discounted_future_transaction_margin,
                'expected_credit_loss': expected_credit_loss,
                'expected_churn_cost': expected_churn_cost,
                'expected_treatment_cost': expected_treatment_cost,
                'expected_tnv': expected_tnv,
                'stability_multiplier': spec['stability_multiplier'],
                'update_direction': spec['update_direction'],
                'validity_days': config_dict[spec['validity_days_key']],
            })
    output_df = pd.DataFrame(action_rows)
    if output_df.empty:
        raise ValueError('No feasible actions were generated in evaluate_tnv_actions')
    return output_df

# -----------------------------------------------------------------------
# Stage 3: Final capacity selection (Layer 4)
# Dimension 4: stability smoothing + network budget constraint.
# -----------------------------------------------------------------------

def select_final_capacity_output(tnv_actions_input_df, config_dict, previous_capacity_df=None, vw6_cap_action_df=None, prior_state_df=None):
    """
    Picks the best action per subscriber and applies:
      1. Policy cap (hard ceiling from config)
      2. Stability multiplier (action-level smoothing, existing)
      3. Stability smoothing (bounded delta from previous cycle, Dimension 4)
      4. Network-wide budget constraint (Dimension 4, optional)
    previous_capacity_df: DataFrame with columns [subscriber_msisdn, CreditLimit]
      from the immediately preceding run. Pass None on first run.
    """
    ranked_df = tnv_actions_input_df.sort_values(
        ['feature_dt', 'subscriber_msisdn', 'expected_tnv', 'target_capacity_raw'],
        ascending=[True, True, False, False]
    ).copy()
    best_df = ranked_df.groupby(['feature_dt', 'subscriber_msisdn'], as_index=False).head(1).copy()

    best_df['selected_action'] = np.where(best_df['expected_tnv'] > 0, best_df['action'], 'DECLINE')
    best_df['target_capacity_policy_capped'] = best_df['target_capacity_raw'].clip(
        lower=0.0, upper=config_dict['policy_capacity_cap']
    )
    best_df['target_capacity_stability_adjusted'] = np.where(
        best_df['selected_action'] == 'DECLINE',
        0.0,
        best_df['target_capacity_policy_capped'] * best_df['stability_multiplier']
    )

    # ------------------------------------------------------------------
    # Dimension 4: Decision stability smoothing — bound change from prior cycle.
    # ------------------------------------------------------------------
    max_inc = config_dict.get('stability_max_increase_pct', 0.25)
    max_dec = config_dict.get('stability_max_decrease_pct', 0.40)

    if previous_capacity_df is not None and not previous_capacity_df.empty:
        prev_df = previous_capacity_df[['subscriber_msisdn', 'CreditLimit']].rename(
            columns={'CreditLimit': 'prev_credit_limit'}
        )
        best_df = best_df.merge(prev_df, on='subscriber_msisdn', how='left')
        best_df['prev_credit_limit'] = best_df['prev_credit_limit'].fillna(0.0)

        # Upper bound: prev × (1 + max_inc)
        upper_bound = best_df['prev_credit_limit'] * (1.0 + max_inc)
        # Lower bound: prev × (1 - max_dec), floored at 0
        lower_bound = (best_df['prev_credit_limit'] * (1.0 - max_dec)).clip(lower=0.0)

        # For first-time subscribers (prev = 0) skip bounding to allow initial grant.
        has_prior = best_df['prev_credit_limit'] > 0
        proposed = best_df['target_capacity_stability_adjusted']

        best_df['target_capacity_stability_adjusted'] = np.where(
            has_prior,
            proposed.clip(lower=lower_bound, upper=upper_bound),
            proposed
        )
    else:
        best_df['prev_credit_limit'] = 0.0

    # ------------------------------------------------------------------
    # Dimension 4: Network-wide budget constraint (optional).
    # Subscribers in priority_states are funded first; others are throttled
    # proportionally if total would exceed budget.
    # ------------------------------------------------------------------
    if config_dict.get('budget_constraint_enabled', False):
        budget_total = config_dict.get('network_lending_budget_total', float('inf'))
        priority_states = config_dict.get('budget_priority_states', [])

        # Sort: priority subscribers first (by expected_tnv descending within each group).
        # Use prior_state_df (from the previous cycle) since current-cycle state
        # is computed in Layers 5-8, which run after the decision engine.
        if prior_state_df is not None and not prior_state_df.empty:
            state_lookup = prior_state_df[['subscriber_msisdn', 'operating_state']].drop_duplicates('subscriber_msisdn')
            best_df = best_df.merge(state_lookup, on='subscriber_msisdn', how='left')
            best_df['operating_state'] = best_df['operating_state'].fillna('')
        else:
            best_df['operating_state'] = ''
        best_df['_is_priority'] = best_df['operating_state'].isin(priority_states).astype(int)
        best_df = best_df.sort_values(['_is_priority', 'expected_tnv'], ascending=[False, False]).reset_index(drop=True)

        cumulative = best_df['target_capacity_stability_adjusted'].cumsum()
        within_budget = cumulative <= budget_total
        # Partially fund the first subscriber that crosses the budget boundary.
        first_over = (~within_budget).idxmax() if (~within_budget).any() else None
        if first_over is not None:
            remaining = max(budget_total - (cumulative.iloc[first_over - 1] if first_over > 0 else 0), 0.0)
            best_df.loc[first_over, 'target_capacity_stability_adjusted'] = remaining
            best_df.loc[first_over + 1:, 'target_capacity_stability_adjusted'] = 0.0

        best_df = best_df.drop(columns=['_is_priority', 'operating_state'], errors='ignore')

    best_df['CreditLimit'] = best_df['target_capacity_stability_adjusted'].round(0)

    # ------------------------------------------------------------------
    # Wire vw6: apply SQL-computed conservative per-subscriber cap.
    # conservative_credit_limit_v1 is a hard ceiling — the TNV optimiser
    # cannot exceed what the SQL view deemed safe for that subscriber.
    # ------------------------------------------------------------------
    if vw6_cap_action_df is not None and not vw6_cap_action_df.empty:
        vw6_clean = vw6_cap_action_df[['feature_dt', 'subscriber_msisdn', 'conservative_credit_limit_v1']].copy()
        vw6_clean['feature_dt'] = pd.to_datetime(vw6_clean['feature_dt'])
        best_df = best_df.merge(vw6_clean, on=['feature_dt', 'subscriber_msisdn'], how='left')
        has_vw6_cap = best_df['conservative_credit_limit_v1'].notna()
        best_df['CreditLimit'] = np.where(
            has_vw6_cap,
            np.minimum(best_df['CreditLimit'], best_df['conservative_credit_limit_v1']),
            best_df['CreditLimit']
        )
        best_df = best_df.drop(columns=['conservative_credit_limit_v1'])

    best_df['output_version'] = config_dict['output_version']
    # Three-value status so downstream systems can distinguish:
    #   declined            — no credit granted (TNV ≤ 0 or first-time reject);
    #                         lender should not disburse
    #   blocked_or_restricted — existing credit relationship now blocked;
    #                         lender must also manage outstanding exposure
    #   approved_or_maintained — CreditLimit > 0, lending permitted
    best_df['decision_status'] = np.select(
        [
            best_df['selected_action'] == 'DECLINE',
            best_df['selected_action'] == 'RESTRICT',
        ],
        ['declined', 'blocked_or_restricted'],
        default='approved_or_maintained'
    )

    final_cols = [
        'feature_dt', 'subscriber_msisdn', 'primary_policy_reason', 'selected_action',
        'decision_status', 'expected_tnv', 'target_capacity_raw', 'target_capacity_policy_capped',
        'target_capacity_stability_adjusted', 'prev_credit_limit', 'CreditLimit',
        'update_direction', 'validity_days', 'output_version'
    ]
    return best_df[[c for c in final_cols if c in best_df.columns]].copy()

# -----------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------

def run_credit_v1_decision_engine(
    layer1_input_df, layer0_input_df,
    config_dict=None, action_specs_input_df=None,
    previous_capacity_df=None,
    vw5_reason_codes_df=None,
    vw6_cap_action_df=None,
    circuit_breaker_multiplier=1.0,
    prior_state_df=None
):
    """
    Runs the full Layer 2–4 decision engine.
    previous_capacity_df: optional DataFrame [subscriber_msisdn, CreditLimit]
      from the prior run, used for stability smoothing. Pass None on first run.
    vw5_reason_codes_df: optional output of vw5_credit_v1_reason_codes.
      When provided, overrides Python-computed primary_policy_reason with the
      SQL-authoritative value for governance reproducibility.
    vw6_cap_action_df: optional output of vw6_credit_v1_cap_and_action.
      When provided, conservative_credit_limit_v1 is applied as a hard
      per-subscriber ceiling below policy_capacity_cap.
    circuit_breaker_multiplier: float emitted by run_layers_5_to_8 portfolio
      monitor. Scales policy_capacity_cap before TNV evaluation. Default 1.0.
    """
    local_config = DECISION_ENGINE_CONFIG.copy() if config_dict is None else config_dict.copy()
    local_action_specs = ACTION_SPECS_DF.copy() if action_specs_input_df is None else action_specs_input_df.copy()

    # Apply portfolio circuit breaker to capacity cap before any evaluation.
    if circuit_breaker_multiplier != 1.0:
        original_cap = local_config['policy_capacity_cap']
        local_config['policy_capacity_cap'] = original_cap * circuit_breaker_multiplier
        print(
            f'Circuit breaker applied: policy_capacity_cap '
            f'{original_cap:.0f} -> {local_config["policy_capacity_cap"]:.0f} '
            f'(multiplier={circuit_breaker_multiplier})'
        )

    decision_base_df = prepare_decision_base(layer1_input_df, layer0_input_df)
    policy_prefilter_df = build_policy_prefilter(decision_base_df, local_config, vw5_reason_codes_df)
    tnv_actions_df = evaluate_tnv_actions(policy_prefilter_df, local_action_specs, local_config)
    final_capacity_df = select_final_capacity_output(tnv_actions_df, local_config, previous_capacity_df, vw6_cap_action_df, prior_state_df)

    return {
        'decision_base_df': decision_base_df,
        'vw7_credit_v1_policy_prefilter': policy_prefilter_df,
        'vw8_credit_v1_tnv_action_evaluation': tnv_actions_df,
        'vw9_credit_v1_final_capacity_output': final_capacity_df
    }

if __name__ == '__main__':
    # Dev/notebook entry point — requires layer1_df and layer0_df in scope.
    engine_outputs = run_credit_v1_decision_engine(layer1_df, layer0_df)
    vw7_credit_v1_policy_prefilter = engine_outputs['vw7_credit_v1_policy_prefilter']
    vw8_credit_v1_tnv_action_evaluation = engine_outputs['vw8_credit_v1_tnv_action_evaluation']
    vw9_credit_v1_final_capacity_output = engine_outputs['vw9_credit_v1_final_capacity_output']

    print(vw7_credit_v1_policy_prefilter[['feature_dt', 'subscriber_msisdn', 'primary_policy_reason', 'feasible_action_set']])
    print(vw8_credit_v1_tnv_action_evaluation.head())
    print(vw9_credit_v1_final_capacity_output)
