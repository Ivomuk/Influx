import os
import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

# Make the src/ package importable without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Suppress tqdm progress bars during tests.
os.environ['TQDM_DISABLE'] = '1'


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

@pytest.fixture
def feature_dt():
    return pd.Timestamp('2026-04-01')


# ---------------------------------------------------------------------------
# Minimal valid layer DataFrames (1 healthy subscriber)
# ---------------------------------------------------------------------------

@pytest.fixture
def layer1_df(feature_dt):
    return pd.DataFrame([{
        'feature_dt': feature_dt,
        'subscriber_msisdn': '256700000001',
        'outstanding_exposure_amt': 0.0,
        'active_lender_cnt_30d': 0,
        'disbursement_cnt_30d': 0,
        'days_since_last_disbursement': None,
        'repayment_ratio_30d': 0.90,
        'repayment_ratio_trend_7d': 0.05,
        'wallet_inflow_amt_30d': 50000.0,
        'wallet_outflow_amt_30d': 20000.0,
        'spend_amt_30d': 15000.0,
        'wallet_active_days_30d': 25,
        'wallet_inflow_trend_7d_vs_90d': 0.05,
        'stacked_borrowing_flag_30d': 0,
        'repeated_borrowing_flag_30d': 0,
        'timing_manipulation_flag': 0,
        'suspicious_repayment_jump_flag': 0,
        'loan_cycling_flag': 0,
    }])


@pytest.fixture
def layer0_df(feature_dt):
    return pd.DataFrame([{
        'feature_dt': feature_dt,
        'subscriber_msisdn': '256700000001',
        'expected_repayment_probability_v1_rule': 0.85,
        'expected_credit_loss_rate_v1_rule': 0.05,
        'churn_cooling_probability_v1_rule': 0.10,
        'expected_future_transaction_margin_score_v1_rule': 0.70,
        'expected_treatment_cost_score_v1_rule': 0.02,
        'customer_lifetime_value_contribution_v1_rule': 0.80,
        'debt_stress_index_v1': 20.0,
        'fraud_abuse_risk_score_v1_rule': 0.05,
        'behavior_consistency_score_v1_rule': 0.90,
        'identity_confidence_score_v1_rule': 0.95,
    }])


# ---------------------------------------------------------------------------
# Decision engine config and action specs
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import DECISION_ENGINE_CONFIG
    return DECISION_ENGINE_CONFIG.copy()


@pytest.fixture
def action_specs_df():
    from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import ACTION_SPECS_DF
    return ACTION_SPECS_DF.copy()


# ---------------------------------------------------------------------------
# Full engine outputs (1 healthy subscriber, no previous capacity)
# ---------------------------------------------------------------------------

@pytest.fixture
def engine_outputs(layer1_df, layer0_df, default_config, action_specs_df):
    from telecom_credit_engine.decisioning.run_credit_v1_decision_engine import run_credit_v1_decision_engine
    return run_credit_v1_decision_engine(
        layer1_df, layer0_df,
        config_dict=default_config,
        action_specs_input_df=action_specs_df,
    )


# ---------------------------------------------------------------------------
# Feature store isolation (clear global dicts before and after each test)
# ---------------------------------------------------------------------------

@pytest.fixture
def clear_feature_store():
    from telecom_credit_engine.feature_store import dim3_feature_store as fs
    fs._hot_store.clear()
    fs._warm_store.clear()
    fs._cold_store.clear()
    yield
    fs._hot_store.clear()
    fs._warm_store.clear()
    fs._cold_store.clear()


# ---------------------------------------------------------------------------
# Lender feedback config (inline; avoids importing dim6_config.py side effects)
# ---------------------------------------------------------------------------

@pytest.fixture
def lender_config():
    return {
        'required_report_fields': ['msisdn', 'loan_id', 'lender_id', 'event_type', 'event_amount', 'event_date'],
        'optional_report_fields': ['days_past_due', 'loan_status', 'loan_tenor_days', 'interest_amount'],
        'valid_event_types': ['DISBURSEMENT', 'REPAYMENT', 'DEFAULT', 'STATUS_CHANGE'],
        'valid_loan_statuses': ['ACTIVE', 'REPAID', 'DEFAULTED', 'RESTRUCTURED', 'WRITTEN_OFF'],
        'reconciliation_amount_tolerance_pct': 0.05,
        'stale_exposure_threshold_minutes': 30,
        'max_single_disbursement_amount': 500_000.0,
        'noncompliance_credit_limit_breach_tolerance': 0.0,
        'output_version': 'test_lender_v1',
    }


@pytest.fixture
def minimal_valid_lender_report():
    return pd.DataFrame([{
        'msisdn': '256700000001',
        'loan_id': 'L001',
        'lender_id': 'LDR001',
        'event_type': 'DISBURSEMENT',
        'event_amount': '10000',
        'event_date': '2026-04-01',
    }])
