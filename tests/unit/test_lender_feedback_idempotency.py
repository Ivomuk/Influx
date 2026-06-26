"""
Tests for idempotency key support in dim6_lender_feedback_api.
Verifies that duplicate events are detected and dropped, and that
the registry correctly tracks processed keys.
"""
import pytest
import pandas as pd

from telecom_credit_engine.interfaces.dim6_lender_feedback_api import (
    validate_lender_report,
    ingest_lender_report,
    get_idempotency_registry,
    clear_idempotency_registry,
    register_idempotency_keys,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_idempotency_registry()
    yield
    clear_idempotency_registry()


def _report(*rows):
    base = {
        'idempotency_key': 'KEY-001',
        'msisdn': '256700000001',
        'loan_id': 'L001',
        'lender_id': 'LDR001',
        'event_type': 'DISBURSEMENT',
        'event_amount': '10000',
        'event_date': '2026-04-01',
    }
    if not rows:
        return pd.DataFrame([base])
    return pd.DataFrame([{**base, **r} for r in rows])


def _cfg():
    return {
        'required_report_fields': [
            'idempotency_key', 'msisdn', 'loan_id', 'lender_id',
            'event_type', 'event_amount', 'event_date',
        ],
        'optional_report_fields': [],
        'valid_event_types': ['DISBURSEMENT', 'REPAYMENT', 'DEFAULT', 'STATUS_CHANGE'],
        'valid_loan_statuses': ['ACTIVE', 'REPAID', 'DEFAULTED', 'RESTRUCTURED', 'WRITTEN_OFF'],
        'reconciliation_amount_tolerance_pct': 0.05,
        'stale_exposure_threshold_minutes': 30,
        'max_single_disbursement_amount': 500000.0,
        'noncompliance_credit_limit_breach_tolerance': 0.0,
        'output_version': 'test_v1',
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_rejects_null_idempotency_key():
    report = _report({'idempotency_key': None})
    validated = validate_lender_report(report, _cfg())
    assert validated.iloc[0]['is_valid_flag'] == 0
    assert 'NULL_IDEMPOTENCY_KEY' in validated.iloc[0]['validation_error_summary']


def test_validate_rejects_blank_idempotency_key():
    report = _report({'idempotency_key': '  '})
    validated = validate_lender_report(report, _cfg())
    assert validated.iloc[0]['is_valid_flag'] == 0
    assert 'NULL_IDEMPOTENCY_KEY' in validated.iloc[0]['validation_error_summary']


def test_validate_accepts_valid_idempotency_key():
    report = _report({'idempotency_key': 'LDR001-L001-DISB-20260401'})
    validated = validate_lender_report(report, _cfg())
    assert validated.iloc[0]['is_valid_flag'] == 1


# ---------------------------------------------------------------------------
# Ingestion deduplication
# ---------------------------------------------------------------------------

def test_first_ingestion_processes_all_rows():
    validated = validate_lender_report(_report(), _cfg())
    ingested = ingest_lender_report(validated, _cfg())
    assert len(ingested) == 1
    assert 'KEY-001' in get_idempotency_registry()


def test_duplicate_submission_drops_all_rows():
    validated = validate_lender_report(_report(), _cfg())
    first = ingest_lender_report(validated, _cfg())
    assert len(first) == 1
    second = ingest_lender_report(validated, _cfg())
    assert len(second) == 0


def test_mixed_new_and_duplicate_keeps_only_new():
    batch1 = _report(
        {'idempotency_key': 'KEY-001'},
        {'idempotency_key': 'KEY-002', 'msisdn': '256700000002'},
    )
    validated1 = validate_lender_report(batch1, _cfg())
    ingested1 = ingest_lender_report(validated1, _cfg())
    assert len(ingested1) == 2

    batch2 = _report(
        {'idempotency_key': 'KEY-002', 'msisdn': '256700000002'},
        {'idempotency_key': 'KEY-003', 'msisdn': '256700000003'},
    )
    validated2 = validate_lender_report(batch2, _cfg())
    ingested2 = ingest_lender_report(validated2, _cfg())
    assert len(ingested2) == 1
    assert ingested2.iloc[0]['msisdn'] == '256700000003'


def test_idempotency_key_appears_in_ingested_output():
    validated = validate_lender_report(_report(), _cfg())
    ingested = ingest_lender_report(validated, _cfg())
    assert 'idempotency_key' in ingested.columns
    assert ingested.iloc[0]['idempotency_key'] == 'KEY-001'


# ---------------------------------------------------------------------------
# Registry management
# ---------------------------------------------------------------------------

def test_clear_registry_resets_state():
    register_idempotency_keys(['A', 'B'])
    assert len(get_idempotency_registry()) == 2
    clear_idempotency_registry()
    assert len(get_idempotency_registry()) == 0


def test_resubmit_after_clear_processes_again():
    validated = validate_lender_report(_report(), _cfg())
    first = ingest_lender_report(validated, _cfg())
    assert len(first) == 1
    clear_idempotency_registry()
    second = ingest_lender_report(validated, _cfg())
    assert len(second) == 1
