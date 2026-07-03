"""
Unit tests for PD model training: synthetic data generation, fitting the
4-model ensemble, and joblib artifact round-tripping.
"""
import pandas as pd
import pytest

from telecom_credit_engine.scoring.pd_features import PD_FEATURE_NAMES
from telecom_credit_engine.scoring.pd_model import load_pd_model_config
from telecom_credit_engine.scoring.pd_model_training import (
    MODEL_NAMES,
    load_pd_models,
    save_pd_models,
    train_all_pd_models,
)
from telecom_credit_engine.scoring.pd_training_data import generate_synthetic_pd_training_data


@pytest.fixture(scope='module')
def pd_config():
    return load_pd_model_config()


@pytest.fixture(scope='module')
def training_df():
    return generate_synthetic_pd_training_data(n_rows=300, seed=7)


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

class TestSyntheticTrainingData:
    def test_shape_and_columns(self, training_df):
        assert len(training_df) == 300
        expected_cols = {'feature_dt', 'subscriber_msisdn', 'did_default_flag'} | set(PD_FEATURE_NAMES)
        assert expected_cols.issubset(set(training_df.columns))

    def test_label_is_binary_and_not_degenerate(self, training_df):
        labels = set(training_df['did_default_flag'].unique())
        assert labels.issubset({0, 1})
        default_rate = training_df['did_default_flag'].mean()
        assert 0.02 < default_rate < 0.98, f'default rate {default_rate} is degenerate'

    def test_deterministic_given_seed(self):
        a = generate_synthetic_pd_training_data(n_rows=50, seed=99)
        b = generate_synthetic_pd_training_data(n_rows=50, seed=99)
        pd.testing.assert_frame_equal(a, b)


# ---------------------------------------------------------------------------
# Fitting all 4 models
# ---------------------------------------------------------------------------

class TestTrainAllPdModels:
    def test_produces_four_fitted_models(self, training_df, pd_config):
        models_dict, metrics_dict = train_all_pd_models(training_df, pd_config)
        assert set(models_dict.keys()) == set(MODEL_NAMES)
        assert set(metrics_dict.keys()) == set(MODEL_NAMES)
        for model in models_dict.values():
            assert hasattr(model, 'predict_proba')

    def test_metrics_report_reasonable_auc(self, training_df, pd_config):
        _, metrics_dict = train_all_pd_models(training_df, pd_config)
        for model_name, metrics in metrics_dict.items():
            assert metrics['n_rows'] == len(training_df)
            # Synthetic data is generatively separable — each model should
            # do meaningfully better than random (AUC 0.5).
            assert metrics['train_auc'] > 0.55, f'{model_name} train_auc={metrics["train_auc"]}'

    def test_metrics_include_out_of_sample_auc(self, training_df, pd_config):
        _, metrics_dict = train_all_pd_models(training_df, pd_config)
        for model_name, metrics in metrics_dict.items():
            assert 'test_auc' in metrics, f'{model_name} missing test_auc'
            assert 0.0 <= metrics['test_auc'] <= 1.0
            assert metrics['test_auc'] > 0.5, f'{model_name} test_auc={metrics["test_auc"]}'
            assert metrics['n_test_rows'] == round(metrics['holdout_fraction'] * len(training_df))


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    def test_round_trip_preserves_predictions(self, training_df, pd_config, tmp_path):
        models_dict, metrics_dict = train_all_pd_models(training_df, pd_config)
        save_pd_models(models_dict, metrics_dict, pd_config, str(tmp_path))

        reloaded_models = load_pd_models(str(tmp_path))
        assert set(reloaded_models.keys()) == set(MODEL_NAMES)

        from telecom_credit_engine.scoring.pd_features import extract_pd_features
        feat_df = extract_pd_features(training_df, pd_config['feature_defaults'], pd_config['feature_names'])
        X = feat_df[pd_config['feature_names']]

        for model_name in MODEL_NAMES:
            original_scores = models_dict[model_name].predict_proba(X)[:, 1]
            reloaded_scores = reloaded_models[model_name].predict_proba(X)[:, 1]
            assert (original_scores == reloaded_scores).all()

    def test_metadata_written(self, training_df, pd_config, tmp_path):
        import json
        models_dict, metrics_dict = train_all_pd_models(training_df, pd_config)
        save_pd_models(models_dict, metrics_dict, pd_config, str(tmp_path))

        with open(tmp_path / 'metadata.json') as fh:
            metadata = json.load(fh)
        assert metadata['model_version'] == pd_config['model_version']
        assert metadata['synthetic_training_data'] is True
        assert set(metadata['metrics'].keys()) == set(MODEL_NAMES)
        assert 'validation_methodology' in metadata
        for metrics in metadata['metrics'].values():
            assert 'test_auc' in metrics
