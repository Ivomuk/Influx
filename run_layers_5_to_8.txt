# Dimension 4 upgrade of Layers 5–8.
# Adds:
#   - State persistence enforcement: upward transitions blocked until minimum
#     persistence window has elapsed (requires prior_state_df with state_change_dt).
#   - DSI hysteresis: Distressed → Recovered upgrade requires DSI below threshold
#     for dsi_hysteresis_days_for_upgrade consecutive evaluation days.
#   - Portfolio circuit breaker feedback: portfolio_control_signal is returned
#     as a capacity multiplier that callers can pass into the decision engine.

import pandas as pd
import numpy as np
from tqdm import tqdm

tqdm.pandas()

# -----------------------------------------------------------------------
# State engine helpers
# -----------------------------------------------------------------------

# Ordered list of states from most to least severe.
# A state is "worse" if it has a lower index.
STATE_SEVERITY = ['Fraud Review', 'Restricted', 'Distressed', 'Cooling Off', 'At Risk', 'Recovered', 'Healthy']

def _is_upward_transition(prior_state, new_state):
    """Returns True if new_state is less severe than prior_state."""
    if prior_state not in STATE_SEVERITY or new_state not in STATE_SEVERITY:
        return False
    return STATE_SEVERITY.index(new_state) > STATE_SEVERITY.index(prior_state)

# -----------------------------------------------------------------------
# Layer 5: Customer State
# -----------------------------------------------------------------------

def build_customer_state_df(policy_prefilter_df, final_capacity_df, state_config, prior_state_df=None):
    """
    Assigns each subscriber an operating state with entry criteria, minimum
    persistence period, allowable actions, and treatment path.

    Dimension 4 persistence enforcement:
      If prior_state_df is supplied and state_config['enforce_minimum_persistence'] is True,
      upward state transitions are blocked when fewer than minimum_persistence_days have
      elapsed since the last state change.

    prior_state_df must contain:
      subscriber_msisdn, operating_state, state_change_dt (date of last transition)
    """
    state_base_df = policy_prefilter_df.merge(
        final_capacity_df, on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one'
    )

    # Raw candidate state from current signals
    state_base_df['candidate_state'] = np.select(
        [
            state_base_df['fraud_abuse_risk_score_v1_rule'] >= state_config['fraud_review_threshold'],
            state_base_df['selected_action'].isin(state_config['restricted_actions']),
            state_base_df['churn_cooling_probability_v1_rule'] >= state_config['cooling_churn_threshold'],
            state_base_df['debt_stress_index_v1'] >= state_config['distressed_dsi_min'],
            (
                (state_base_df['repayment_ratio_30d'] >= state_config['recovered_repayment_min'])
                & (state_base_df['debt_stress_index_v1'] <= state_config['recovered_dsi_max'])
                & (state_base_df['selected_action'] == 'MAINTAIN')
            ),
            state_base_df['debt_stress_index_v1'].between(state_config['at_risk_dsi_min'], state_config['at_risk_dsi_max']),
            state_base_df['debt_stress_index_v1'] <= state_config['healthy_dsi_max'],
        ],
        ['Fraud Review', 'Restricted', 'Cooling Off', 'Distressed', 'Recovered', 'At Risk', 'Healthy'],
        default='At Risk'
    )

    # ------------------------------------------------------------------
    # Dimension 4: Persistence enforcement
    # ------------------------------------------------------------------
    enforce = state_config.get('enforce_minimum_persistence', True)

    if prior_state_df is not None and not prior_state_df.empty:
        required_prior_cols = ['subscriber_msisdn', 'operating_state']
        missing = [c for c in required_prior_cols if c not in prior_state_df.columns]
        if missing:
            raise ValueError('prior_state_df missing columns: ' + ', '.join(missing))

        prior_df = prior_state_df[['subscriber_msisdn', 'operating_state']].copy()
        prior_df = prior_df.rename(columns={'operating_state': 'prior_operating_state'})

        has_change_dt = 'state_change_dt' in prior_state_df.columns
        if has_change_dt:
            prior_df['state_change_dt'] = pd.to_datetime(prior_state_df['state_change_dt'])

        state_base_df = state_base_df.merge(prior_df, on='subscriber_msisdn', how='left')

        if enforce and has_change_dt:
            state_base_df['feature_dt_dt'] = pd.to_datetime(state_base_df['feature_dt'])
            state_base_df['days_in_current_state'] = (
                state_base_df['feature_dt_dt'] - state_base_df['state_change_dt']
            ).dt.days.fillna(999)

            # Per-state minimum persistence
            def _get_min_persistence(state_val):
                if state_val == 'Fraud Review':
                    return state_config['minimum_persistence_days_fraud_review']
                if state_val == 'Restricted':
                    return state_config['minimum_persistence_days_restricted']
                return state_config['minimum_persistence_days_default']

            state_base_df['min_persistence'] = state_base_df['prior_operating_state'].apply(
                lambda s: _get_min_persistence(s) if pd.notna(s) else 0
            )

            # Block upward transitions that happen too early
            too_early = (
                state_base_df['days_in_current_state'] < state_base_df['min_persistence']
            )
            is_upward = state_base_df.apply(
                lambda row: _is_upward_transition(row['prior_operating_state'], row['candidate_state'])
                if pd.notna(row.get('prior_operating_state')) else False,
                axis=1
            )
            # Revert to prior state when transition is upward and too early
            state_base_df['operating_state'] = np.where(
                too_early & is_upward,
                state_base_df['prior_operating_state'],
                state_base_df['candidate_state']
            )
            state_base_df['persistence_block_applied'] = (too_early & is_upward).astype(int)
            state_base_df = state_base_df.drop(columns=['feature_dt_dt', 'min_persistence'])
        else:
            state_base_df['operating_state'] = state_base_df['candidate_state']
            state_base_df['persistence_block_applied'] = 0

    else:
        state_base_df['prior_operating_state'] = pd.NA
        state_base_df['operating_state'] = state_base_df['candidate_state']
        state_base_df['persistence_block_applied'] = 0

    # State change date: set to feature_dt when state changed, carry forward otherwise
    if prior_state_df is not None and 'state_change_dt' in prior_state_df.columns and not prior_state_df.empty:
        state_base_df['state_change_dt'] = np.where(
            state_base_df['operating_state'] != state_base_df['prior_operating_state'],
            state_base_df['feature_dt'].astype(str),
            state_base_df.get('state_change_dt', state_base_df['feature_dt']).astype(str)
        )
    else:
        state_base_df['state_change_dt'] = state_base_df['feature_dt'].astype(str)

    # Entry criteria, persistence, allowable actions, treatment path
    state_base_df['entry_criteria_code'] = np.select(
        [
            state_base_df['operating_state'] == 'Fraud Review',
            state_base_df['operating_state'] == 'Restricted',
            state_base_df['operating_state'] == 'Cooling Off',
            state_base_df['operating_state'] == 'Distressed',
            state_base_df['operating_state'] == 'Recovered',
            state_base_df['operating_state'] == 'At Risk',
            state_base_df['operating_state'] == 'Healthy',
        ],
        ['FRAUD_SCORE_TRIGGER', 'POLICY_RESTRICTION_TRIGGER', 'COOLING_CHURN_TRIGGER',
         'HIGH_DSI_TRIGGER', 'RECOVERY_STABILIZATION_TRIGGER', 'EARLY_WARNING_TRIGGER',
         'LOW_RISK_STABLE_TRIGGER'],
        default='DEFAULT_TRIGGER'
    )

    state_base_df['minimum_persistence_days'] = np.select(
        [state_base_df['operating_state'] == 'Fraud Review',
         state_base_df['operating_state'] == 'Restricted'],
        [state_config['minimum_persistence_days_fraud_review'],
         state_config['minimum_persistence_days_restricted']],
        default=state_config['minimum_persistence_days_default']
    )

    state_base_df['allowable_actions'] = np.select(
        [
            state_base_df['operating_state'] == 'Fraud Review',
            state_base_df['operating_state'] == 'Restricted',
            state_base_df['operating_state'] == 'Cooling Off',
            state_base_df['operating_state'] == 'Distressed',
            state_base_df['operating_state'] == 'Recovered',
            state_base_df['operating_state'] == 'At Risk',
            state_base_df['operating_state'] == 'Healthy',
        ],
        ['RESTRICT_ONLY', 'DECLINE_OR_RESTRICT', 'REDUCE_OR_MAINTAIN', 'REDUCE_OR_MAINTAIN',
         'MAINTAIN_OR_INCREASE_SMALL', 'MAINTAIN_OR_REDUCE', 'MAINTAIN_OR_INCREASE'],
        default='MAINTAIN_OR_REDUCE'
    )

    state_base_df['treatment_path'] = np.select(
        [
            state_base_df['operating_state'] == 'Fraud Review',
            state_base_df['operating_state'] == 'Restricted',
            state_base_df['operating_state'] == 'Cooling Off',
            state_base_df['operating_state'] == 'Distressed',
            state_base_df['operating_state'] == 'Recovered',
            state_base_df['operating_state'] == 'At Risk',
            state_base_df['operating_state'] == 'Healthy',
        ],
        ['MANUAL_REVIEW_AND_BLOCK', 'PROTECTIVE_RESTRICTION_AND_REEVALUATION',
         'COOLDOWN_AND_REACTIVATION_MONITORING', 'RECOVERY_AND_RESTRUCTURING_PATH',
         'GRADUAL_RESTORATION_PATH', 'PREEMPTIVE_MONITORING_PATH', 'STANDARD_GROWTH_PATH'],
        default='PREEMPTIVE_MONITORING_PATH'
    )

    output_cols = [
        'feature_dt', 'subscriber_msisdn', 'operating_state', 'prior_operating_state',
        'entry_criteria_code', 'minimum_persistence_days', 'allowable_actions',
        'treatment_path', 'selected_action', 'CreditLimit',
        'state_change_dt', 'persistence_block_applied'
    ]
    return state_base_df[[c for c in output_cols if c in state_base_df.columns]].copy()

# -----------------------------------------------------------------------
# Layer 6: Intervention
# -----------------------------------------------------------------------

def build_intervention_df(policy_prefilter_df, state_df, intervention_config):
    base_df = policy_prefilter_df.merge(
        state_df[['feature_dt', 'subscriber_msisdn', 'operating_state']],
        on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one'
    )

    base_df['wallet_drop_flag'] = (
        base_df['churn_cooling_probability_v1_rule'] >= intervention_config['wallet_drop_threshold']
    ).astype(int)
    base_df['rising_dsi_flag'] = (base_df['debt_stress_index_v1'] >= 60.0).astype(int)
    base_df['missed_repayment_flag'] = (
        base_df['repayment_ratio_30d'].fillna(0) < intervention_config['missed_repayment_threshold']
    ).astype(int)
    base_df['unusual_repeat_borrowing_flag'] = (
        base_df['repeated_borrowing_flag_30d'] == intervention_config['repeat_borrowing_flag_value']
    ).astype(int)
    base_df['repeated_cooling_off_flag'] = (
        base_df['churn_cooling_probability_v1_rule'] >= intervention_config['cooling_pattern_threshold']
    ).astype(int)
    base_df['recovery_behavior_flag'] = (
        (base_df['repayment_ratio_30d'].fillna(0) >= intervention_config['recovery_probability_min'])
        & (base_df['debt_stress_index_v1'] < 45.0)
    ).astype(int)

    base_df['intervention_action'] = np.select(
        [
            base_df['operating_state'] == 'Fraud Review',
            base_df['operating_state'] == 'Restricted',
            base_df['operating_state'] == 'Distressed',
            base_df['operating_state'] == 'Cooling Off',
            base_df['recovery_behavior_flag'] == 1,
            base_df['operating_state'] == 'At Risk',
        ],
        ['TEMPORARY_BLOCK_AND_MANUAL_REVIEW', 'SCHEDULED_REEVALUATION_AND_RESTRICTION',
         'RESTRUCTURING_RECOMMENDATION', 'REACTIVATION_TREATMENT',
         'GRADUAL_RESTORATION', 'CAPACITY_REDUCTION_AND_MONITORING'],
        default='STANDARD_MONITORING'
    )

    trigger_pairs = [
        ('wallet_drop', 'wallet_drop_flag'),
        ('rising_dsi', 'rising_dsi_flag'),
        ('missed_repayment', 'missed_repayment_flag'),
        ('unusual_repeat_borrowing', 'unusual_repeat_borrowing_flag'),
        ('repeated_cooling_off', 'repeated_cooling_off_flag'),
        ('recovery_behavior', 'recovery_behavior_flag'),
    ]
    base_df['trigger_summary'] = base_df.apply(
        lambda row: ','.join([name for name, col in trigger_pairs if row[col] == 1]),
        axis=1
    )

    return base_df[[
        'feature_dt', 'subscriber_msisdn', 'operating_state', 'intervention_action',
        'trigger_summary', 'wallet_drop_flag', 'rising_dsi_flag', 'missed_repayment_flag',
        'unusual_repeat_borrowing_flag', 'repeated_cooling_off_flag', 'recovery_behavior_flag'
    ]].copy()

# -----------------------------------------------------------------------
# Layer 7: Portfolio Monitor
# Dimension 4: returns circuit_breaker_capacity_multiplier for caller use.
# -----------------------------------------------------------------------

def build_portfolio_monitor_df(policy_prefilter_df, state_df, final_capacity_df, portfolio_config):
    """
    Monitors portfolio-level health and emits a portfolio_control_signal per date.
    Dimension 4: also emits circuit_breaker_capacity_multiplier, which callers
    should pass into subsequent decision engine runs via config_dict['policy_capacity_cap'].
    """
    portfolio_base_df = (
        policy_prefilter_df
        .merge(state_df[['feature_dt', 'subscriber_msisdn', 'operating_state']],
               on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one')
        .merge(final_capacity_df[['feature_dt', 'subscriber_msisdn', 'CreditLimit', 'selected_action']],
               on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one')
    )

    cb_enabled = portfolio_config.get('circuit_breaker_enabled', True)
    cb_multipliers = portfolio_config.get('circuit_breaker_multipliers', {})

    grouped_rows = []
    for feature_dt_val, group_df in portfolio_base_df.groupby('feature_dt'):
        total_rows = max(len(group_df), 1)
        total_credit_limit = group_df['CreditLimit'].fillna(0).sum()
        top_n = max(int(np.ceil(total_rows * 0.20)), 1)
        top_exposure = group_df['CreditLimit'].fillna(0).sort_values(ascending=False).head(top_n).sum()
        exposure_concentration = top_exposure / total_credit_limit if total_credit_limit > 0 else 0.0

        restrict_rate = group_df['selected_action'].isin(['DECLINE', 'RESTRICT']).mean()
        distressed_rate = (group_df['operating_state'] == 'Distressed').mean()
        fraud_review_rate = (group_df['operating_state'] == 'Fraud Review').mean()
        stacked_rate = (group_df.get('stacked_borrowing_flag_30d', 0) == 1).mean()

        grouped_rows.append({
            'feature_dt': feature_dt_val,
            'subscriber_count': total_rows,
            'total_credit_limit': total_credit_limit,
            'avg_dsi': group_df['debt_stress_index_v1'].mean(),
            'distressed_rate': distressed_rate,
            'cooling_off_rate': (group_df['operating_state'] == 'Cooling Off').mean(),
            'fraud_review_rate': fraud_review_rate,
            'restrict_rate': restrict_rate,
            'stacked_borrowing_rate': stacked_rate,
            'exposure_concentration_top20pct': exposure_concentration,
        })

    monitor_df = pd.DataFrame(grouped_rows)

    monitor_df['portfolio_control_signal'] = np.select(
        [
            monitor_df['restrict_rate'] >= portfolio_config['high_restrict_rate_threshold'],
            monitor_df['distressed_rate'] >= portfolio_config['high_distressed_rate_threshold'],
            monitor_df['fraud_review_rate'] >= portfolio_config['high_fraud_review_rate_threshold'],
            monitor_df['exposure_concentration_top20pct'] >= portfolio_config['high_exposure_concentration_threshold'],
            monitor_df['stacked_borrowing_rate'] >= portfolio_config['high_stacked_borrowing_rate_threshold'],
        ],
        ['SYSTEM_WIDE_TIGHTENING', 'DISTRESS_POSTURE_TIGHTENING', 'FRAUD_PRESSURE_TIGHTENING',
         'EXPOSURE_CAP_REBALANCING', 'STACKING_CAP_TIGHTENING'],
        default='STABLE_PORTFOLIO'
    )

    # ------------------------------------------------------------------
    # Dimension 4: Circuit breaker capacity multiplier.
    # ------------------------------------------------------------------
    if cb_enabled and cb_multipliers:
        monitor_df['circuit_breaker_capacity_multiplier'] = monitor_df['portfolio_control_signal'].map(
            cb_multipliers
        ).fillna(1.0)
    else:
        monitor_df['circuit_breaker_capacity_multiplier'] = 1.0

    return monitor_df

# -----------------------------------------------------------------------
# Layer 8: Governance Log
# -----------------------------------------------------------------------

def build_governance_log_df(policy_prefilter_df, state_df, final_capacity_df, governance_config):
    governance_df = (
        policy_prefilter_df
        .merge(state_df[['feature_dt', 'subscriber_msisdn', 'operating_state', 'treatment_path',
                          'persistence_block_applied']],
               on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one')
        .merge(final_capacity_df[['feature_dt', 'subscriber_msisdn', 'selected_action',
                                   'CreditLimit', 'decision_status', 'prev_credit_limit']],
               on=['feature_dt', 'subscriber_msisdn'], how='left', validate='one_to_one')
    )

    governance_df['decision_engine_version'] = governance_config['decision_engine_version']
    governance_df['state_engine_version'] = governance_config['state_engine_version']

    governance_df['manual_review_required_flag'] = (
        (governance_df['operating_state'].isin(['Fraud Review', 'Restricted']))
        | (governance_df['CreditLimit'].fillna(0) >= 15000)
    ).astype(int)

    governance_df['manual_review_queue'] = np.where(
        governance_df['manual_review_required_flag'] == 1, 'exception_handling_queue', 'none'
    )
    governance_df['override_permission_roles'] = np.where(
        governance_df['manual_review_required_flag'] == 1,
        governance_config['oversight_queue_roles'], 'none'
    )
    governance_df['audit_trigger_flag'] = (
        (governance_df['manual_review_required_flag'] == 1)
        & (governance_df['selected_action'].isin(['DECLINE', 'RESTRICT']))
    ).astype(int)

    governance_df['oversight_note'] = np.where(
        governance_df['manual_review_required_flag'] == 1,
        'CONTROLLED_EXCEPTION_PATH_ONLY', 'AUTOMATION_PRIMARY'
    )

    output_cols = [
        'feature_dt', 'subscriber_msisdn', 'operating_state', 'treatment_path',
        'selected_action', 'CreditLimit', 'prev_credit_limit', 'decision_status',
        'primary_policy_reason', 'persistence_block_applied',
        'decision_engine_version', 'state_engine_version',
        'manual_review_required_flag', 'manual_review_queue',
        'override_permission_roles', 'audit_trigger_flag', 'oversight_note'
    ]
    return governance_df[[c for c in output_cols if c in governance_df.columns]].copy()

# -----------------------------------------------------------------------
# Orchestrator
# -----------------------------------------------------------------------

def run_layers_5_to_8(
    policy_prefilter_df, final_capacity_df,
    state_config=None, intervention_config=None,
    portfolio_config=None, governance_config=None,
    prior_state_df=None
):
    """
    Runs Layers 5–8 in sequence.
    Returns a dict of all output DataFrames plus the circuit_breaker_capacity_multiplier
    for the most recent feature_dt (to be applied in the next decision engine run).
    """
    local_state_config = STATE_ENGINE_CONFIG.copy() if state_config is None else state_config.copy()
    local_intervention_config = INTERVENTION_CONFIG.copy() if intervention_config is None else intervention_config.copy()
    local_portfolio_config = PORTFOLIO_CONFIG.copy() if portfolio_config is None else portfolio_config.copy()
    local_governance_config = GOVERNANCE_CONFIG.copy() if governance_config is None else governance_config.copy()

    state_df = build_customer_state_df(
        policy_prefilter_df, final_capacity_df, local_state_config, prior_state_df
    )
    intervention_df = build_intervention_df(
        policy_prefilter_df, state_df, local_intervention_config
    )
    portfolio_monitor_df = build_portfolio_monitor_df(
        policy_prefilter_df, state_df, final_capacity_df, local_portfolio_config
    )
    governance_log_df = build_governance_log_df(
        policy_prefilter_df, state_df, final_capacity_df, local_governance_config
    )

    # Surface the circuit breaker multiplier for the latest date so callers
    # can apply it to the next decision engine run's policy_capacity_cap.
    latest_monitor = portfolio_monitor_df.sort_values('feature_dt').iloc[-1] if not portfolio_monitor_df.empty else {}
    cb_multiplier = float(latest_monitor.get('circuit_breaker_capacity_multiplier', 1.0))
    cb_signal = str(latest_monitor.get('portfolio_control_signal', 'STABLE_PORTFOLIO'))

    if cb_multiplier < 1.0:
        print(f'PORTFOLIO ALERT: {cb_signal} — capacity multiplier applied: {cb_multiplier:.2f}')

    return {
        'state_df': state_df,
        'intervention_df': intervention_df,
        'portfolio_monitor_df': portfolio_monitor_df,
        'governance_log_df': governance_log_df,
        'circuit_breaker_capacity_multiplier': cb_multiplier,
        'portfolio_control_signal': cb_signal,
    }

# -----------------------------------------------------------------------
# Execute on in-memory outputs from the decision engine run above.
# -----------------------------------------------------------------------
layer_5_to_8_outputs = run_layers_5_to_8(
    vw7_credit_v1_policy_prefilter,
    vw9_credit_v1_final_capacity_output
)
state_df = layer_5_to_8_outputs['state_df']
intervention_df = layer_5_to_8_outputs['intervention_df']
portfolio_monitor_df = layer_5_to_8_outputs['portfolio_monitor_df']
governance_log_df = layer_5_to_8_outputs['governance_log_df']

print(state_df)
print(intervention_df)
print(portfolio_monitor_df[['feature_dt', 'portfolio_control_signal', 'circuit_breaker_capacity_multiplier']])
print(governance_log_df[['feature_dt', 'subscriber_msisdn', 'operating_state', 'selected_action',
                           'CreditLimit', 'persistence_block_applied', 'audit_trigger_flag']])
