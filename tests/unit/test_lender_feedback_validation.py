"""Tests for the enhanced lender feedback validation checks in dim6_lender_feedback_api."""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from telecom_credit_engine.interfaces.dim6_lender_feedback_api import (
    validate_lender_report,
    ingest_lender_report,
    flag_repayment_exceeds_exposure,
    flag_impossible_event_ordering,
    clear_idempotency_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_idempotency():
    """Reset the idempotency registry before every test."""
    clear_idempotency_registry()


@pytest.fixture
def config():
    return {
        'required_report_fields': [
            'idempotency_key', 'msisdn', 'loan_id', 'lender_id',
            'event_type', 'event_amount', 'event_date',
        ],
        'optional_report_fields': [],
        'valid_event_types': ['DISBURSEMENT', 'REPAYMENT', 'DEFAULT', 'STATUS_CHANGE'],
        'valid_loan_statuses': ['ACTIVE', 'REPAID', 'DEFAULTED', 'RESTRUCTURED', 'WRITTEN_OFF'],
        'max_single_disbursement_amount': 500_000.0,
        'output_version': 'test_v1',
    }


def _make_report(**overrides):
    """Build a single-row report DataFrame with sensible defaults."""
    row = {
        'idempotency_key': 'key-001',
        'msisdn': '254700000001',
        'loan_id': 'LN-001',
        'lender_id': 'LENDER-A',
        'event_type': 'DISBURSEMENT',
        'event_amount': 1000.00,
        'event_date': '2025-01-15',
    }
    row.update(overrides)
    return pd.DataFrame([row])


# ---------------------------------------------------------------------------
# 1. Future-dated event
# ---------------------------------------------------------------------------

class TestFutureDatedEvent:

    def test_future_event_rejected(self, config):
        tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
        df = _make_report(event_date=tomorrow)
        result = validate_lender_report(df, config)
        assert result['is_valid_flag'].iloc[0] == 0
        assert 'FUTURE_DATED_EVENT' in result['validation_error_summary'].iloc[0]

    def test_same_day_event_accepted(self, config):
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        df = _make_report(event_date=today)
        result = validate_lender_report(df, config)
        assert 'FUTURE_DATED_EVENT' not in result['validation_error_summary'].iloc[0]
        assert result['is_valid_flag'].iloc[0] == 1


# ---------------------------------------------------------------------------
# 2. Duplicate disbursement in batch
# ---------------------------------------------------------------------------

class TestDuplicateDisbursementInBatch:

    def test_duplicate_disbursement_flagged_second_only(self, config):
        row = {
            'idempotency_key': ['k1', 'k2'],
            'msisdn': ['254700000001', '254700000001'],
            'loan_id': ['LN-001', 'LN-001'],
            'lender_id': ['LENDER-A', 'LENDER-A'],
            'event_type': ['DISBURSEMENT', 'DISBURSEMENT'],
            'event_amount': [1000.00, 1000.00],
            'event_date': ['2025-01-15', '2025-01-15'],
        }
        df = pd.DataFrame(row)
        result = validate_lender_report(df, config)
        # First occurrence should be valid
        assert result['is_valid_flag'].iloc[0] == 1
        # Second occurrence should be flagged
        assert 'DUPLICATE_DISBURSEMENT_IN_BATCH' in result['validation_error_summary'].iloc[1]

    def test_different_amounts_not_flagged(self, config):
        row = {
            'idempotency_key': ['k1', 'k2'],
            'msisdn': ['254700000001', '254700000001'],
            'loan_id': ['LN-001', 'LN-001'],
            'lender_id': ['LENDER-A', 'LENDER-A'],
            'event_type': ['DISBURSEMENT', 'DISBURSEMENT'],
            'event_amount': [1000.00, 2000.00],
            'event_date': ['2025-01-15', '2025-01-15'],
        }
        df = pd.DataFrame(row)
        result = validate_lender_report(df, config)
        for i in range(len(result)):
            assert 'DUPLICATE_DISBURSEMENT_IN_BATCH' not in result['validation_error_summary'].iloc[i]


# ---------------------------------------------------------------------------
# 3. Invalid amount precision
# ---------------------------------------------------------------------------

class TestInvalidAmountPrecision:

    def test_too_many_decimals_flagged(self, config):
        df = _make_report(event_amount=1000.123)
        result = validate_lender_report(df, config)
        assert result['is_valid_flag'].iloc[0] == 0
        assert 'INVALID_AMOUNT_PRECISION' in result['validation_error_summary'].iloc[0]

    def test_two_decimals_accepted(self, config):
        df = _make_report(event_amount=1000.12)
        result = validate_lender_report(df, config)
        assert 'INVALID_AMOUNT_PRECISION' not in result['validation_error_summary'].iloc[0]
        assert result['is_valid_flag'].iloc[0] == 1


# ---------------------------------------------------------------------------
# 4. Repayment exceeds exposure
# ---------------------------------------------------------------------------

class TestRepaymentExceedsExposure:

    def _ingest(self, report_df, config):
        validated = validate_lender_report(report_df, config)
        return ingest_lender_report(validated, config)

    def test_repayment_exceeding_exposure_flagged(self, config):
        df = _make_report(
            event_type='REPAYMENT',
            event_amount=5000.00,
        )
        ingested = self._ingest(df, config)
        exposure = pd.DataFrame({
            'msisdn': ['254700000001'],
            'outstanding_exposure_amt': [1000.00],
        })
        warnings = flag_repayment_exceeds_exposure(ingested, exposure)
        assert not warnings.empty
        assert (warnings['warning_code'] == 'REPAYMENT_EXCEEDS_EXPOSURE').all()

    def test_repayment_within_exposure_not_flagged(self, config):
        df = _make_report(
            event_type='REPAYMENT',
            event_amount=500.00,
        )
        ingested = self._ingest(df, config)
        exposure = pd.DataFrame({
            'msisdn': ['254700000001'],
            'outstanding_exposure_amt': [1000.00],
        })
        warnings = flag_repayment_exceeds_exposure(ingested, exposure)
        assert warnings.empty


# ---------------------------------------------------------------------------
# 5. Impossible event ordering
# ---------------------------------------------------------------------------

class TestImpossibleEventOrdering:

    def _ingest(self, report_df, config):
        validated = validate_lender_report(report_df, config)
        return ingest_lender_report(validated, config)

    def test_repayment_before_disbursement_flagged(self, config):
        rows = {
            'idempotency_key': ['k1', 'k2'],
            'msisdn': ['254700000001', '254700000001'],
            'loan_id': ['LN-001', 'LN-001'],
            'lender_id': ['LENDER-A', 'LENDER-A'],
            'event_type': ['REPAYMENT', 'DISBURSEMENT'],
            'event_amount': [500.00, 1000.00],
            'event_date': ['2025-01-10', '2025-01-15'],
        }
        df = pd.DataFrame(rows)
        ingested = self._ingest(df, config)
        warnings = flag_impossible_event_ordering(ingested)
        assert not warnings.empty
        assert (warnings['warning_code'] == 'REPAYMENT_BEFORE_DISBURSEMENT').all()

    def test_normal_ordering_not_flagged(self, config):
        rows = {
            'idempotency_key': ['k1', 'k2'],
            'msisdn': ['254700000001', '254700000001'],
            'loan_id': ['LN-001', 'LN-001'],
            'lender_id': ['LENDER-A', 'LENDER-A'],
            'event_type': ['DISBURSEMENT', 'REPAYMENT'],
            'event_amount': [1000.00, 500.00],
            'event_date': ['2025-01-10', '2025-01-15'],
        }
        df = pd.DataFrame(rows)
        ingested = self._ingest(df, config)
        warnings = flag_impossible_event_ordering(ingested)
        assert warnings.empty
