# Operating Modes — named incident-time states that modify pipeline behaviour.
#
# Modes (ascending severity):
#   NORMAL              — all paths active, standard thresholds
#   DEGRADED            — feature store partially stale; widen staleness tolerance
#   CONSERVATIVE        — portfolio stress detected; cap scaled to 80 % of base
#   MANUAL_REVIEW_ONLY  — severe stress; no capacity increases, flag for human sign-off
#   FREEZE              — no new credit capacity; existing limits held unchanged
#
# detect_operating_mode() derives the current mode from observable signals:
#   circuit_breaker_multiplier, portfolio_metrics, feature_store_health, certification result.
#
# apply_operating_mode_overrides() returns a modified config dict copy for the mode.
# Both are called by the orchestrator before run_credit_v1_decision_engine each cycle.

import pandas as pd
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Mode constants
# ---------------------------------------------------------------------------

OPERATING_MODE_NORMAL             = 'NORMAL'
OPERATING_MODE_DEGRADED           = 'DEGRADED'
OPERATING_MODE_CONSERVATIVE       = 'CONSERVATIVE'
OPERATING_MODE_MANUAL_REVIEW_ONLY = 'MANUAL_REVIEW_ONLY'
OPERATING_MODE_FREEZE             = 'FREEZE'

VALID_OPERATING_MODES = {
    OPERATING_MODE_NORMAL,
    OPERATING_MODE_DEGRADED,
    OPERATING_MODE_CONSERVATIVE,
    OPERATING_MODE_MANUAL_REVIEW_ONLY,
    OPERATING_MODE_FREEZE,
}

_MODE_SEVERITY = {
    OPERATING_MODE_NORMAL:             0,
    OPERATING_MODE_DEGRADED:           1,
    OPERATING_MODE_CONSERVATIVE:       2,
    OPERATING_MODE_MANUAL_REVIEW_ONLY: 3,
    OPERATING_MODE_FREEZE:             4,
}


# ---------------------------------------------------------------------------
# Detection thresholds
# ---------------------------------------------------------------------------

OPERATING_MODE_THRESHOLDS = {
    # Portfolio distress rates
    'distressed_rate_conservative':         0.15,
    'distressed_rate_manual_review':        0.25,
    'distressed_rate_freeze':               0.40,
    'fraud_review_rate_conservative':       0.10,
    'fraud_review_rate_manual_review':      0.15,
    'fraud_review_rate_freeze':             0.20,
    # Circuit breaker multiplier bands
    'circuit_breaker_conservative':         0.75,
    'circuit_breaker_manual_review':        0.65,
    # Feature store hot-path maximum age that triggers DEGRADED
    'feature_store_stale_minutes_degraded': 30,
    # Mode applied when certification fails
    'certification_failed_mode':            OPERATING_MODE_DEGRADED,
}

# ---------------------------------------------------------------------------
# Per-mode config overrides
# None entries are computed dynamically in apply_operating_mode_overrides.
# ---------------------------------------------------------------------------

OPERATING_MODE_CONFIG_OVERRIDES = {
    OPERATING_MODE_NORMAL: {},
    OPERATING_MODE_DEGRADED: {
        'stale_path_threshold_minutes': 60,
    },
    OPERATING_MODE_CONSERVATIVE: {
        'policy_capacity_cap':        None,   # base_cap * 0.80 computed dynamically
        'stability_max_increase_pct': 0.10,
    },
    OPERATING_MODE_MANUAL_REVIEW_ONLY: {
        'policy_capacity_cap':        None,   # base_cap * 0.65 computed dynamically
        'stability_max_increase_pct': 0.0,
        'manual_review_required':     True,
    },
    OPERATING_MODE_FREEZE: {
        'policy_capacity_cap':        0,
        'stability_max_increase_pct': 0.0,
        'stability_max_decrease_pct': 0.0,
        'manual_review_required':     True,
        'freeze_active':              True,
    },
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_operating_mode(
    portfolio_metrics=None,
    feature_store_health_df=None,
    circuit_breaker_multiplier=1.0,
    certification_passed=True,
    manual_override=None,
):
    """
    Derives the current operating mode from observable signals.
    Returns (mode_string, reasons_list).

    portfolio_metrics      : dict from build_portfolio_monitor_df
                             (keys: distressed_rate, fraud_review_rate, …)
    feature_store_health_df: DataFrame from get_store_health()
    circuit_breaker_multiplier: float from prior portfolio run
    certification_passed   : bool from assert_certified()
    manual_override        : if a valid mode string, returned immediately (operator override)
    """
    if manual_override is not None:
        if manual_override not in VALID_OPERATING_MODES:
            raise ValueError(f'Invalid manual_override mode: {manual_override}')
        return manual_override, [f'MANUAL_OVERRIDE={manual_override}']

    mode    = OPERATING_MODE_NORMAL
    reasons = []
    t       = OPERATING_MODE_THRESHOLDS

    # --- Feature store staleness ---
    if feature_store_health_df is not None and not feature_store_health_df.empty:
        hot = feature_store_health_df[feature_store_health_df['path'] == 'hot']
        if not hot.empty:
            max_age = hot.iloc[0].get('max_age_minutes')
            if max_age is not None and max_age > t['feature_store_stale_minutes_degraded']:
                mode = _escalate(mode, OPERATING_MODE_DEGRADED)
                reasons.append(f'HOT_PATH_STALE: max_age={max_age:.1f}min')

    # --- Certification failure ---
    if not certification_passed:
        mode = _escalate(mode, t['certification_failed_mode'])
        reasons.append('CERTIFICATION_FAILED')

    # --- Circuit breaker ---
    if circuit_breaker_multiplier <= t['circuit_breaker_manual_review']:
        mode = _escalate(mode, OPERATING_MODE_MANUAL_REVIEW_ONLY)
        reasons.append(f'CIRCUIT_BREAKER_CRITICAL: multiplier={circuit_breaker_multiplier}')
    elif circuit_breaker_multiplier <= t['circuit_breaker_conservative']:
        mode = _escalate(mode, OPERATING_MODE_CONSERVATIVE)
        reasons.append(f'CIRCUIT_BREAKER_STRESSED: multiplier={circuit_breaker_multiplier}')

    # --- Portfolio distress ---
    if portfolio_metrics:
        dr = portfolio_metrics.get('distressed_rate', 0)
        fr = portfolio_metrics.get('fraud_review_rate', 0)

        if dr > t['distressed_rate_freeze']:
            mode = _escalate(mode, OPERATING_MODE_FREEZE)
            reasons.append(f'DISTRESSED_RATE_CRITICAL: {dr:.1%}')
        elif dr > t['distressed_rate_manual_review']:
            mode = _escalate(mode, OPERATING_MODE_MANUAL_REVIEW_ONLY)
            reasons.append(f'DISTRESSED_RATE_HIGH: {dr:.1%}')
        elif dr > t['distressed_rate_conservative']:
            mode = _escalate(mode, OPERATING_MODE_CONSERVATIVE)
            reasons.append(f'DISTRESSED_RATE_ELEVATED: {dr:.1%}')

        if fr > t['fraud_review_rate_freeze']:
            mode = _escalate(mode, OPERATING_MODE_FREEZE)
            reasons.append(f'FRAUD_RATE_CRITICAL: {fr:.1%}')
        elif fr > t['fraud_review_rate_manual_review']:
            mode = _escalate(mode, OPERATING_MODE_MANUAL_REVIEW_ONLY)
            reasons.append(f'FRAUD_RATE_HIGH: {fr:.1%}')
        elif fr > t['fraud_review_rate_conservative']:
            mode = _escalate(mode, OPERATING_MODE_CONSERVATIVE)
            reasons.append(f'FRAUD_RATE_ELEVATED: {fr:.1%}')

    if not reasons:
        reasons = ['ALL_SIGNALS_NOMINAL']

    print(f'Operating mode: {mode} | Reasons: {reasons}')
    return mode, reasons


def _escalate(current, candidate):
    return candidate if _MODE_SEVERITY[candidate] > _MODE_SEVERITY[current] else current


# ---------------------------------------------------------------------------
# Config override application
# ---------------------------------------------------------------------------

def apply_operating_mode_overrides(base_config_dict, operating_mode, circuit_breaker_multiplier=1.0):
    """
    Returns a modified copy of base_config_dict with mode-specific overrides applied.
    Does not mutate the input dict.
    Dynamic capacity caps are computed as a fraction of the base policy_capacity_cap.
    """
    if operating_mode not in VALID_OPERATING_MODES:
        raise ValueError(f'Unknown operating mode: {operating_mode}')

    config    = base_config_dict.copy()
    overrides = OPERATING_MODE_CONFIG_OVERRIDES[operating_mode].copy()
    base_cap  = base_config_dict.get('policy_capacity_cap', float('inf'))

    if operating_mode == OPERATING_MODE_CONSERVATIVE and overrides.get('policy_capacity_cap') is None:
        overrides['policy_capacity_cap'] = base_cap * min(circuit_breaker_multiplier, 0.80)
    elif operating_mode == OPERATING_MODE_MANUAL_REVIEW_ONLY and overrides.get('policy_capacity_cap') is None:
        overrides['policy_capacity_cap'] = base_cap * min(circuit_breaker_multiplier, 0.65)

    config.update({k: v for k, v in overrides.items() if v is not None})

    if operating_mode != OPERATING_MODE_NORMAL:
        print(f'Mode overrides applied [{operating_mode}]: {[k for k, v in overrides.items() if v is not None]}')
    return config


# ---------------------------------------------------------------------------
# Governance log entry
# ---------------------------------------------------------------------------

def build_mode_log_entry(operating_mode, reasons, execution_dt=None):
    """Returns a dict suitable for appending to the governance log."""
    return {
        'log_dt':          (execution_dt or datetime.now(timezone.utc)).isoformat(),
        'operating_mode':  operating_mode,
        'mode_reasons':    '; '.join(reasons),
    }
