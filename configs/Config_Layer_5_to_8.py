# Build reusable modules for Layer 5 to Layer 8 on top of the in-memory decision-engine outputs.
import pandas as pd
import numpy as np
from tqdm import tqdm

tqdm.pandas()

STATE_ENGINE_CONFIG = {
    'healthy_dsi_max': 35.0,
    'at_risk_dsi_min': 36.0,
    'at_risk_dsi_max': 59.0,
    'distressed_dsi_min': 60.0,
    'cooling_churn_threshold': 0.65,
    'recovered_repayment_min': 0.75,
    'recovered_dsi_max': 45.0,
    'fraud_review_threshold': 0.80,
    'restricted_actions': ['DECLINE', 'RESTRICT'],
    'minimum_persistence_days_default': 7,
    'minimum_persistence_days_restricted': 14,
    'minimum_persistence_days_fraud_review': 14,

    # -----------------------------------------------------------------------
    # Dimension 4: Persistence enforcement — prevent upward state transitions
    # before the minimum persistence window has elapsed.
    # Set enforce_minimum_persistence = False to disable (e.g. during testing).
    # -----------------------------------------------------------------------
    'enforce_minimum_persistence': True,
    # DSI must be below the healthy threshold for this many consecutive days
    # before a Distressed → Recovered upgrade is permitted.
    'dsi_hysteresis_days_for_upgrade': 3,
}

INTERVENTION_CONFIG = {
    'wallet_drop_threshold': 0.40,
    'dsi_rise_threshold': 15.0,
    'missed_repayment_threshold': 0.25,
    'repeat_borrowing_flag_value': 1,
    'cooling_pattern_threshold': 0.65,
    'recovery_probability_min': 0.70
}

PORTFOLIO_CONFIG = {
    'high_restrict_rate_threshold': 0.35,
    'high_distressed_rate_threshold': 0.25,
    'high_fraud_review_rate_threshold': 0.05,
    'high_exposure_concentration_threshold': 0.40,
    'high_stacked_borrowing_rate_threshold': 0.20,

    # -----------------------------------------------------------------------
    # Dimension 4: Portfolio circuit breaker — when a signal fires, reduce
    # the effective policy_capacity_cap by the corresponding multiplier.
    # A multiplier of 0.70 means new approvals are capped at 70% of the
    # standard policy_capacity_cap until the signal clears.
    # -----------------------------------------------------------------------
    'circuit_breaker_enabled': True,
    'circuit_breaker_multipliers': {
        'SYSTEM_WIDE_TIGHTENING':    0.60,
        'DISTRESS_POSTURE_TIGHTENING': 0.70,
        'FRAUD_PRESSURE_TIGHTENING': 0.65,
        'EXPOSURE_CAP_REBALANCING':  0.75,
        'STACKING_CAP_TIGHTENING':   0.80,
        'STABLE_PORTFOLIO':          1.00,
    }
}

GOVERNANCE_CONFIG = {
    'decision_engine_version': 'credit_v1_python_decision_engine_prod',
    'state_engine_version': 'credit_v1_state_engine_v1',
    'oversight_queue_roles': 'risk_ops,collections_ops,governance_manager',
    'manual_review_trigger_actions': 'RESTRICT,FRAUD_REVIEW,HIGH_VALUE_EDGE_CASE'
}

if __name__ == '__main__':
    print(pd.Series(STATE_ENGINE_CONFIG))
    print(pd.Series(INTERVENTION_CONFIG))