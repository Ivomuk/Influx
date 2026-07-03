"""
Unit tests for the PD model (shadow/challenger ensemble) scoring module.
Uses the real trained artifacts committed under models/pd_v1/ so scoring is
exercised against actual fitted estimators, matching how the orchestrator
step (run_pd_model_scoring_step) uses them in production.
"""
import os

import pandas as pd
import pytest

from telecom_credit_engine.scoring.pd_model import (
    PdModelConfigError,
    load_pd_model_config,
    score_pd_model_ensemble,
)
from telecom_credit_engine.scoring.pd_model_training import (
    MODEL_NAMES,
    PdModelArtifactError,
    load_pd_models,
)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_MODEL_DIR = os.path.join(_REPO_ROOT, 'models', 'pd_v1')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _l1_row(**overrides):
    row = {
        'feature_dt': pd.Timestamp('2026-04-01'),
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
    }
    row.update(overrides)
    return pd.DataFrame([row])


@pytest.fixture(scope='module')
def pd_config():
    return load_pd_model_config()


@pytest.fixture(scope='module')
def pd_models():
    return load_pd_models(_MODEL_DIR)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring:
    def test_output_shape_and_columns(self, pd_config, pd_models):
        result = score_pd_model_ensemble(_l1_row(), pd_models, pd_config)
        assert len(result) == 1
        expected_cols = {
            'feature_dt', 'subscriber_msisdn', 'pd_score_v1_model',
            'pd_score_logreg_v1', 'pd_score_rf_v1', 'pd_score_xgb_v1', 'pd_score_lgbm_v1',
            'pd_model_version',
        }
        assert expected_cols.issubset(set(result.columns))

    def test_scores_are_bounded(self, pd_config, pd_models):
        result = score_pd_model_ensemble(_l1_row(), pd_models, pd_config)
        for col in ('pd_score_v1_model', 'pd_score_logreg_v1', 'pd_score_rf_v1', 'pd_score_xgb_v1', 'pd_score_lgbm_v1'):
            assert (result[col] > 0).all()
            assert (result[col] < 1).all()

    def test_model_version_matches_config(self, pd_config, pd_models):
        result = score_pd_model_ensemble(_l1_row(), pd_models, pd_config)
        assert result['pd_model_version'].iloc[0] == pd_config['model_version']

    def test_row_count_matches_input(self, pd_config, pd_models):
        layer1_df = pd.concat([_l1_row(subscriber_msisdn='256700000001'),
                                _l1_row(subscriber_msisdn='256700000002')], ignore_index=True)
        result = score_pd_model_ensemble(layer1_df, pd_models, pd_config)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Monotonicity — ensemble score should move in the expected risk direction
# ---------------------------------------------------------------------------

class TestMonotonicity:
    def test_lower_repayment_ratio_scores_higher_pd(self, pd_config, pd_models):
        good = score_pd_model_ensemble(_l1_row(repayment_ratio_30d=0.95), pd_models, pd_config)
        bad = score_pd_model_ensemble(_l1_row(repayment_ratio_30d=0.20), pd_models, pd_config)
        assert bad['pd_score_v1_model'].iloc[0] > good['pd_score_v1_model'].iloc[0]

    def test_stacked_borrowing_flag_increases_pd(self, pd_config, pd_models):
        baseline = score_pd_model_ensemble(_l1_row(stacked_borrowing_flag_30d=0), pd_models, pd_config)
        stacked = score_pd_model_ensemble(_l1_row(stacked_borrowing_flag_30d=1, active_lender_cnt_30d=2), pd_models, pd_config)
        assert stacked['pd_score_v1_model'].iloc[0] > baseline['pd_score_v1_model'].iloc[0]

    def test_loan_cycling_flag_increases_pd(self, pd_config, pd_models):
        baseline = score_pd_model_ensemble(_l1_row(loan_cycling_flag=0), pd_models, pd_config)
        cycling = score_pd_model_ensemble(_l1_row(loan_cycling_flag=1), pd_models, pd_config)
        assert cycling['pd_score_v1_model'].iloc[0] > baseline['pd_score_v1_model'].iloc[0]


# ---------------------------------------------------------------------------
# Missing-column tolerance — must work against the bare
# LAYER1_FEATURES_CONTRACT-minimum shape (no repayment_ratio_60d/90d,
# no sim_age_days, etc.)
# ---------------------------------------------------------------------------

class TestMissingColumnTolerance:
    def test_scores_bare_contract_minimum_layer1_df(self, pd_config, pd_models):
        # _l1_row() above already matches the LAYER1_FEATURES_CONTRACT-only shape.
        result = score_pd_model_ensemble(_l1_row(), pd_models, pd_config)
        assert not result['pd_score_v1_model'].isna().any()
        assert (result['pd_score_v1_model'] > 0).all() and (result['pd_score_v1_model'] < 1).all()


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfigLoading:
    def test_loads_successfully(self, pd_config):
        assert isinstance(pd_config, dict)
        assert pd_config['model_version'] == 'pd_v1_ensemble_2026-07-03'

    def test_bookkeeping_keys_attached(self, pd_config):
        assert '_config_hash' in pd_config
        assert len(pd_config['_config_hash']) == 64
        assert '_config_loaded_at' in pd_config

    def test_feature_defaults_match_feature_names(self, pd_config):
        assert set(pd_config['feature_defaults'].keys()) == set(pd_config['feature_names'])

    def test_ensemble_weights_match_model_names(self, pd_config):
        assert set(pd_config['ensemble_weights'].keys()) == set(MODEL_NAMES)

    def test_missing_required_key_raises(self, tmp_path):
        import yaml
        raw = {
            'model_version': 'x',
            'feature_names': ['repayment_ratio_30d'],
            'feature_defaults': {'repayment_ratio_30d': 0.5},
            'ensemble_weights': {name: 0.25 for name in MODEL_NAMES},
            'disagreement_threshold': 0.25,
            # missing: correlation_warning_threshold, model_hyperparameters
        }
        path = tmp_path / 'bad_pd_config.yaml'
        with open(path, 'w') as fh:
            yaml.dump(raw, fh)
        with pytest.raises(PdModelConfigError, match='correlation_warning_threshold|model_hyperparameters'):
            load_pd_model_config(str(path))

    def test_feature_defaults_mismatch_raises(self, tmp_path):
        import yaml
        raw = {
            'model_version': 'x',
            'feature_names': ['repayment_ratio_30d', 'loan_cycling_flag'],
            'feature_defaults': {'repayment_ratio_30d': 0.5},  # missing loan_cycling_flag
            'ensemble_weights': {name: 0.25 for name in MODEL_NAMES},
            'disagreement_threshold': 0.25,
            'correlation_warning_threshold': 0.98,
            'model_hyperparameters': {},
        }
        path = tmp_path / 'mismatch_pd_config.yaml'
        with open(path, 'w') as fh:
            yaml.dump(raw, fh)
        with pytest.raises(PdModelConfigError, match='feature_defaults'):
            load_pd_model_config(str(path))


# ---------------------------------------------------------------------------
# Model artifact loading
# ---------------------------------------------------------------------------

class TestModelArtifactLoading:
    def test_loads_all_four_models(self, pd_models):
        assert set(pd_models.keys()) == set(MODEL_NAMES)
        for model in pd_models.values():
            assert hasattr(model, 'predict_proba')

    def test_missing_artifact_raises(self, tmp_path):
        with pytest.raises(PdModelArtifactError, match='Missing PD model artifact'):
            load_pd_models(str(tmp_path))
