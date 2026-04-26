"""
Unit tests for telecom_credit_engine.governance.operating_modes.
Covers detect_operating_mode, apply_operating_mode_overrides, and build_mode_log_entry.
"""
import pytest
import pandas as pd

from telecom_credit_engine.governance.operating_modes import (
    detect_operating_mode,
    apply_operating_mode_overrides,
    build_mode_log_entry,
    OPERATING_MODE_NORMAL,
    OPERATING_MODE_DEGRADED,
    OPERATING_MODE_CONSERVATIVE,
    OPERATING_MODE_MANUAL_REVIEW_ONLY,
    OPERATING_MODE_FREEZE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _health_df(max_age_minutes, path='hot'):
    return pd.DataFrame([{
        'path': path,
        'record_count': 10,
        'max_age_minutes': max_age_minutes,
    }])


def _portfolio(distressed_rate=0.0, fraud_review_rate=0.0):
    return {'distressed_rate': distressed_rate, 'fraud_review_rate': fraud_review_rate}


_BASE_CONFIG = {
    'policy_capacity_cap': 20000.0,
    'stability_max_increase_pct': 0.25,
    'stability_max_decrease_pct': 0.40,
}


# ---------------------------------------------------------------------------
# detect_operating_mode — nominal
# ---------------------------------------------------------------------------

def test_all_nominal_signals_returns_normal():
    mode, reasons = detect_operating_mode()
    assert mode == OPERATING_MODE_NORMAL
    assert reasons == ['ALL_SIGNALS_NOMINAL']


def test_stale_hot_path_triggers_degraded():
    mode, reasons = detect_operating_mode(
        feature_store_health_df=_health_df(max_age_minutes=45)
    )
    assert mode == OPERATING_MODE_DEGRADED
    assert any('HOT_PATH_STALE' in r for r in reasons)


def test_certification_failure_triggers_degraded():
    mode, reasons = detect_operating_mode(certification_passed=False)
    assert mode == OPERATING_MODE_DEGRADED
    assert 'CERTIFICATION_FAILED' in reasons


def test_circuit_breaker_0_70_triggers_conservative():
    mode, reasons = detect_operating_mode(circuit_breaker_multiplier=0.70)
    assert mode == OPERATING_MODE_CONSERVATIVE
    assert any('CIRCUIT_BREAKER_STRESSED' in r for r in reasons)


def test_circuit_breaker_0_60_triggers_manual_review():
    mode, reasons = detect_operating_mode(circuit_breaker_multiplier=0.60)
    assert mode == OPERATING_MODE_MANUAL_REVIEW_ONLY
    assert any('CIRCUIT_BREAKER_CRITICAL' in r for r in reasons)


def test_distressed_rate_0_18_triggers_conservative():
    mode, _ = detect_operating_mode(portfolio_metrics=_portfolio(distressed_rate=0.18))
    assert mode == OPERATING_MODE_CONSERVATIVE


def test_distressed_rate_0_30_triggers_manual_review():
    mode, reasons = detect_operating_mode(portfolio_metrics=_portfolio(distressed_rate=0.30))
    assert mode == OPERATING_MODE_MANUAL_REVIEW_ONLY
    assert any('DISTRESSED_RATE_HIGH' in r for r in reasons)


def test_distressed_rate_0_45_triggers_freeze():
    mode, reasons = detect_operating_mode(portfolio_metrics=_portfolio(distressed_rate=0.45))
    assert mode == OPERATING_MODE_FREEZE
    assert any('DISTRESSED_RATE_CRITICAL' in r for r in reasons)


def test_fraud_rate_0_25_triggers_freeze():
    mode, reasons = detect_operating_mode(portfolio_metrics=_portfolio(fraud_review_rate=0.25))
    assert mode == OPERATING_MODE_FREEZE
    assert any('FRAUD_RATE_CRITICAL' in r for r in reasons)


def test_escalation_picks_highest_severity():
    # cert fail → DEGRADED, circuit breaker 0.60 → MANUAL_REVIEW; result = MANUAL_REVIEW
    mode, reasons = detect_operating_mode(
        certification_passed=False,
        circuit_breaker_multiplier=0.60,
    )
    assert mode == OPERATING_MODE_MANUAL_REVIEW_ONLY
    assert len(reasons) == 2


def test_manual_override_returns_immediately():
    mode, reasons = detect_operating_mode(
        certification_passed=False,
        circuit_breaker_multiplier=0.40,
        manual_override=OPERATING_MODE_FREEZE,
    )
    assert mode == OPERATING_MODE_FREEZE
    assert reasons == [f'MANUAL_OVERRIDE={OPERATING_MODE_FREEZE}']


def test_invalid_manual_override_raises():
    with pytest.raises(ValueError, match='INVALID'):
        detect_operating_mode(manual_override='INVALID')


# ---------------------------------------------------------------------------
# apply_operating_mode_overrides
# ---------------------------------------------------------------------------

def test_apply_overrides_normal_no_changes():
    cfg = _BASE_CONFIG.copy()
    result = apply_operating_mode_overrides(cfg, OPERATING_MODE_NORMAL)
    assert result['policy_capacity_cap'] == cfg['policy_capacity_cap']
    assert result['stability_max_increase_pct'] == cfg['stability_max_increase_pct']


def test_apply_overrides_conservative_scales_cap():
    cfg = _BASE_CONFIG.copy()
    result = apply_operating_mode_overrides(cfg, OPERATING_MODE_CONSERVATIVE, circuit_breaker_multiplier=1.0)
    # cap * min(1.0, 0.80) = 20000 * 0.80 = 16000
    assert result['policy_capacity_cap'] == pytest.approx(16000.0)
    assert result['stability_max_increase_pct'] == 0.10


def test_apply_overrides_conservative_clamps_cb_at_0_80():
    cfg = _BASE_CONFIG.copy()
    result = apply_operating_mode_overrides(cfg, OPERATING_MODE_CONSERVATIVE, circuit_breaker_multiplier=0.60)
    # min(0.60, 0.80) = 0.60 → 20000 * 0.60 = 12000
    assert result['policy_capacity_cap'] == pytest.approx(12000.0)


def test_apply_overrides_manual_review_scales_cap():
    cfg = _BASE_CONFIG.copy()
    result = apply_operating_mode_overrides(cfg, OPERATING_MODE_MANUAL_REVIEW_ONLY, circuit_breaker_multiplier=1.0)
    # cap * min(1.0, 0.65) = 20000 * 0.65 = 13000
    assert result['policy_capacity_cap'] == pytest.approx(13000.0)
    assert result['stability_max_increase_pct'] == 0.0


def test_apply_overrides_freeze_sets_cap_zero():
    cfg = _BASE_CONFIG.copy()
    result = apply_operating_mode_overrides(cfg, OPERATING_MODE_FREEZE)
    assert result['policy_capacity_cap'] == 0
    assert result['stability_max_increase_pct'] == 0.0
    assert result['stability_max_decrease_pct'] == 0.0
    assert result.get('freeze_active') is True


def test_apply_overrides_does_not_mutate_input():
    cfg = _BASE_CONFIG.copy()
    original_cap = cfg['policy_capacity_cap']
    apply_operating_mode_overrides(cfg, OPERATING_MODE_CONSERVATIVE)
    assert cfg['policy_capacity_cap'] == original_cap


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match='BANANA'):
        apply_operating_mode_overrides(_BASE_CONFIG.copy(), 'BANANA')


# ---------------------------------------------------------------------------
# build_mode_log_entry
# ---------------------------------------------------------------------------

def test_build_mode_log_entry_shape():
    entry = build_mode_log_entry(OPERATING_MODE_CONSERVATIVE, ['CIRCUIT_BREAKER_STRESSED: multiplier=0.72'])
    assert 'log_dt' in entry
    assert entry['operating_mode'] == OPERATING_MODE_CONSERVATIVE
    assert 'CIRCUIT_BREAKER_STRESSED' in entry['mode_reasons']
