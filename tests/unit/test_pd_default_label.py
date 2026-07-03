"""
Unit tests for the obligor-level, materiality-thresholded default definition:
identify_default_events() and its wiring through compute_realised_outcomes(),
plus the optional-field plumbing in ingest_lender_report() that feeds it.
"""
import numpy as np
import pandas as pd

from telecom_credit_engine.monitoring.dim6_outcome_tracking import (
    compute_pd_model_accuracy,
    compute_realised_outcomes,
    identify_default_events,
)
from telecom_credit_engine.interfaces.dim6_lender_feedback_api import (
    clear_idempotency_registry,
    ingest_lender_report,
    validate_lender_report,
)


LABEL_CONFIG = {
    'default_materiality_abs_floor': 100.0,
    'default_materiality_rel_pct': 0.01,
    'default_materiality_min_dpd': 90,
    'utp_loan_statuses': ['DEFAULTED', 'WRITTEN_OFF', 'RESTRUCTURED'],
}

OUTCOME_CONFIG = {
    'outcome_lookback_days': 30,
    'pd_default_horizon_days': 365,
    **LABEL_CONFIG,
}


def _default_event(**overrides):
    row = {
        'msisdn': '256700000001',
        'event_date': '2026-01-15',
        'event_amount': 500.0,
        'is_default': 1,
        'days_past_due': 120.0,
        'loan_status': None,
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# identify_default_events — materiality and UTP limbs
# ---------------------------------------------------------------------------

class TestIdentifyDefaultEvents:
    def test_material_default_triggers(self):
        events = pd.DataFrame([_default_event()])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 1

    def test_sub_threshold_amount_does_not_trigger(self):
        events = pd.DataFrame([_default_event(event_amount=50.0)])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 0

    def test_relative_materiality_leg_uses_credit_limit(self):
        # 60 is below the 100 absolute floor but above 1% of a 5000 limit.
        events = pd.DataFrame([_default_event(event_amount=60.0)])
        assert len(identify_default_events(events, LABEL_CONFIG, credit_limit=5000.0)) == 1
        assert len(identify_default_events(events, LABEL_CONFIG, credit_limit=None)) == 0

    def test_dpd_below_90_does_not_trigger_even_if_material(self):
        events = pd.DataFrame([_default_event(days_past_due=45.0)])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 0

    def test_missing_dpd_falls_back_to_amount_test(self):
        material = pd.DataFrame([_default_event(days_past_due=np.nan)])
        immaterial = pd.DataFrame([_default_event(days_past_due=np.nan, event_amount=50.0)])
        assert len(identify_default_events(material, LABEL_CONFIG)) == 1
        assert len(identify_default_events(immaterial, LABEL_CONFIG)) == 0

    def test_utp_status_triggers_regardless_of_amount_and_dpd(self):
        events = pd.DataFrame([
            _default_event(event_amount=10.0, days_past_due=5.0, loan_status='WRITTEN_OFF'),
        ])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 1

    def test_utp_on_non_default_event_triggers(self):
        # A STATUS_CHANGE reporting a write-off is a UTP trigger even though
        # is_default == 0.
        events = pd.DataFrame([
            _default_event(is_default=0, event_amount=10.0, loan_status='RESTRUCTURED'),
        ])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 1

    def test_missing_optional_columns_tolerated(self):
        events = pd.DataFrame([{
            'msisdn': '256700000001',
            'event_date': '2026-01-15',
            'event_amount': 500.0,
            'is_default': 1,
        }])
        assert len(identify_default_events(events, LABEL_CONFIG)) == 1

    def test_empty_frame_returns_empty(self):
        assert identify_default_events(pd.DataFrame(), LABEL_CONFIG).empty


# ---------------------------------------------------------------------------
# compute_realised_outcomes — multi-product contagion and censoring
# ---------------------------------------------------------------------------

def _snapshot(feature_dt='2026-01-01', msisdn='256700000001', credit_limit=10000.0):
    return pd.DataFrame([{
        'feature_dt': pd.Timestamp(feature_dt),
        'subscriber_msisdn': msisdn,
        'CreditLimit': credit_limit,
        'pd_score_v1_model': 0.30,
    }])


def _feedback(rows):
    df = pd.DataFrame(rows)
    df['is_repayment'] = df.get('is_repayment', 0)
    return df


class TestComputeRealisedOutcomesLabel:
    def test_small_default_on_one_product_clean_other_gives_zero(self):
        # Product A: immaterial default; product B: clean (repayment only).
        feedback = _feedback([
            _default_event(event_amount=20.0, days_past_due=np.nan, event_date='2026-02-01'),
            {'msisdn': '256700000001', 'event_date': '2026-01-20', 'event_amount': 3000.0,
             'is_default': 0, 'is_repayment': 1, 'days_past_due': np.nan, 'loan_status': None},
        ])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2027-02-01', OUTCOME_CONFIG)
        assert outcomes['did_default_flag'].iloc[0] == 0
        assert outcomes['did_repay_flag'].iloc[0] == 1

    def test_material_default_on_one_product_marks_obligor_defaulted(self):
        # Contagion: material default on product A defaults the whole obligor
        # even though product B repaid cleanly.
        feedback = _feedback([
            _default_event(event_amount=800.0, event_date='2026-03-01'),
            {'msisdn': '256700000001', 'event_date': '2026-01-20', 'event_amount': 3000.0,
             'is_default': 0, 'is_repayment': 1, 'days_past_due': np.nan, 'loan_status': None},
        ])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2027-02-01', OUTCOME_CONFIG)
        assert outcomes['did_default_flag'].iloc[0] == 1
        assert outcomes['material_default_amount'].iloc[0] == 800.0

    def test_default_within_pd_horizon_but_outside_repayment_window_counts(self):
        # Event at day ~150: outside the 30-day repayment window, inside the
        # 365-day PD horizon.
        feedback = _feedback([_default_event(event_date='2026-06-01')])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2027-02-01', OUTCOME_CONFIG)
        assert outcomes['did_default_flag'].iloc[0] == 1

    def test_censored_snapshot_flagged_immature(self):
        # Old enough for the 30-day repayment window, not for the 365-day
        # PD horizon.
        feedback = _feedback([_default_event(event_date='2026-02-01')])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2026-03-15', OUTCOME_CONFIG)
        assert outcomes['pd_label_mature_flag'].iloc[0] == 0

    def test_mature_snapshot_flagged(self):
        feedback = _feedback([_default_event(event_date='2026-02-01')])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2027-02-01', OUTCOME_CONFIG)
        assert outcomes['pd_label_mature_flag'].iloc[0] == 1

    def test_feedback_without_optional_columns_still_works(self):
        feedback = pd.DataFrame([{
            'msisdn': '256700000001', 'event_date': '2026-02-01',
            'event_amount': 800.0, 'is_default': 1, 'is_repayment': 0,
        }])
        outcomes = compute_realised_outcomes(_snapshot(), feedback, '2027-02-01', OUTCOME_CONFIG)
        assert outcomes['did_default_flag'].iloc[0] == 1


class TestPdAccuracyCensoring:
    def test_immature_labels_excluded_from_calibration(self):
        outcomes = pd.DataFrame({
            'predicted_pd_score_v1_model': [0.1, 0.2, 0.3, 0.4],
            'did_default_flag': [0, 0, 1, 1],
            'pd_label_mature_flag': [0, 0, 0, 0],
        })
        config = {'pd_calibration_bucket_count': 2, 'pd_calibration_min_bucket_size': 1}
        assert compute_pd_model_accuracy(outcomes, config).empty

    def test_mature_labels_included(self):
        outcomes = pd.DataFrame({
            'predicted_pd_score_v1_model': [0.1, 0.2, 0.3, 0.4],
            'did_default_flag': [0, 0, 1, 1],
            'pd_label_mature_flag': [1, 1, 1, 1],
        })
        config = {'pd_calibration_bucket_count': 2, 'pd_calibration_min_bucket_size': 1}
        assert not compute_pd_model_accuracy(outcomes, config).empty


# ---------------------------------------------------------------------------
# Ingestion plumbing — optional risk fields must reach outcome tracking
# ---------------------------------------------------------------------------

class TestOptionalFieldPlumbing:
    def test_days_past_due_and_loan_status_survive_ingestion(self, lender_config):
        clear_idempotency_registry()
        report = pd.DataFrame([{
            'idempotency_key': 'LDR001-L001-DEF-20260401',
            'msisdn': '256700000001',
            'loan_id': 'L001',
            'lender_id': 'LDR001',
            'event_type': 'DEFAULT',
            'event_amount': '800',
            'event_date': '2026-04-01',
            'days_past_due': '95',
            'loan_status': 'DEFAULTED',
        }])
        validated = validate_lender_report(report, lender_config)
        assert validated['is_valid_flag'].iloc[0] == 1
        ingested = ingest_lender_report(validated, lender_config)
        assert 'days_past_due' in ingested.columns
        assert 'loan_status' in ingested.columns
        assert ingested['days_past_due'].iloc[0] == 95.0
        assert ingested['loan_status'].iloc[0] == 'DEFAULTED'
        clear_idempotency_registry()

    def test_ingestion_without_optional_fields_yields_nullable_columns(self, lender_config):
        clear_idempotency_registry()
        report = pd.DataFrame([{
            'idempotency_key': 'LDR001-L002-DEF-20260401',
            'msisdn': '256700000001',
            'loan_id': 'L002',
            'lender_id': 'LDR001',
            'event_type': 'DEFAULT',
            'event_amount': '800',
            'event_date': '2026-04-01',
        }])
        validated = validate_lender_report(report, lender_config)
        ingested = ingest_lender_report(validated, lender_config)
        assert 'days_past_due' in ingested.columns
        assert 'loan_status' in ingested.columns
        assert pd.isna(ingested['days_past_due'].iloc[0])
        clear_idempotency_registry()
