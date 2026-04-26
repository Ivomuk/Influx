# Build production-grade reusable functions for the Layer 2 to Layer 4 engine.
import pandas as pd
import numpy as np
from tqdm import tqdm

tqdm.pandas()

# Production-style configuration for deterministic v1 decisioning.
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

    # -----------------------------------------------------------------------
    # Dimension 4: Decision stability — bound how fast capacity can move
    # between consecutive evaluation cycles.
    # -----------------------------------------------------------------------
    # Maximum fraction CreditLimit may increase in one cycle (e.g. 0.25 = +25%).
    'stability_max_increase_pct': 0.25,
    # Maximum fraction CreditLimit may decrease in one cycle (e.g. 0.40 = -40%).
    'stability_max_decrease_pct': 0.40,
    # Minimum days that must separate a RESTRICT → non-RESTRICT transition.
    'stability_restrict_cooldown_days': 14,

    # -----------------------------------------------------------------------
    # Dimension 4: Network-wide lending budget constraint.
    # Set budget_constraint_enabled = False to disable until calibrated.
    # -----------------------------------------------------------------------
    'budget_constraint_enabled': False,
    # Maximum total CreditLimit the system may approve in a single run (UGX).
    'network_lending_budget_total': 50_000_000.0,
    # Segments that receive priority when budget is constrained.
    # Subscribers outside these states are throttled first.
    'budget_priority_states': ['Healthy', 'Recovered'],

    # -----------------------------------------------------------------------
    # Dimension 4: Anti-gaming policy blocks (feeds from vw4 flags).
    # -----------------------------------------------------------------------
    # Block lending when timing_manipulation_flag is set.
    'block_timing_manipulation': True,
    # Block lending when loan_cycling_flag is set.
    'block_loan_cycling': True,
}

ACTION_SPECS_DF = pd.DataFrame([
    {'action':'DECLINE', 'capacity_multiplier':0.00, 'base_margin_rate':0.00, 'churn_relief':0.02, 'treatment_multiplier':0.20, 'stability_multiplier':0.00, 'update_direction':'restrict', 'validity_days_key':'validity_days_restrict'},
    {'action':'RESTRICT', 'capacity_multiplier':0.00, 'base_margin_rate':0.00, 'churn_relief':0.05, 'treatment_multiplier':0.30, 'stability_multiplier':0.00, 'update_direction':'restrict', 'validity_days_key':'validity_days_restrict'},
    {'action':'REDUCE', 'capacity_multiplier':0.50, 'base_margin_rate':0.08, 'churn_relief':0.01, 'treatment_multiplier':0.60, 'stability_multiplier':0.90, 'update_direction':'reduce', 'validity_days_key':'validity_days_reduce'},
    {'action':'MAINTAIN', 'capacity_multiplier':1.00, 'base_margin_rate':0.10, 'churn_relief':0.00, 'treatment_multiplier':1.00, 'stability_multiplier':1.00, 'update_direction':'maintain', 'validity_days_key':'validity_days_maintain'},
    {'action':'INCREASE_SMALL', 'capacity_multiplier':1.25, 'base_margin_rate':0.11, 'churn_relief':-0.01, 'treatment_multiplier':1.10, 'stability_multiplier':0.95, 'update_direction':'increase', 'validity_days_key':'validity_days_increase'},
    {'action':'INCREASE_MEDIUM', 'capacity_multiplier':1.50, 'base_margin_rate':0.12, 'churn_relief':-0.02, 'treatment_multiplier':1.25, 'stability_multiplier':0.90, 'update_direction':'increase', 'validity_days_key':'validity_days_increase'}
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

print(ACTION_SPECS_DF)
print(pd.Series(DECISION_ENGINE_CONFIG))