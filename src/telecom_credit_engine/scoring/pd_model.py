# Probability-of-Default (PD) model — shadow/challenger ensemble scoring.
#
# Combines 4 trained models (logistic regression, random forest, XGBoost,
# LightGBM) into pd_score_v1_model, a weighted-average ensemble PD estimate.
# This score is computed, logged, and monitored (see
# monitoring/dim6_outcome_tracking.py) but does NOT feed
# decisioning/run_credit_v1_decision_engine.py's evaluate_tnv_actions() —
# it must not influence CreditLimit until promoted out of shadow mode.
#
# Individual model scores are retained (pd_score_<name>_v1) for model-risk
# monitoring: correlation and disagreement checks across the 4 challengers,
# per the README's "Model risk controls" (model correlation monitoring,
# challenger models, disagreement detection).

import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from telecom_credit_engine.config_loader import compute_config_hash
from telecom_credit_engine.scoring.pd_features import extract_pd_features
from telecom_credit_engine.scoring.pd_model_training import MODEL_NAMES

_REQUIRED_PD_CONFIG_KEYS = frozenset([
    'model_version',
    'feature_names',
    'feature_defaults',
    'ensemble_weights',
    'disagreement_threshold',
    'correlation_warning_threshold',
    'model_hyperparameters',
])

_SCORE_COLUMN_BY_MODEL = {name: f'pd_score_{name}_v1' for name in MODEL_NAMES}


class PdModelConfigError(Exception):
    pass


def load_pd_model_config(yaml_path=None):
    """
    Loads and validates the PD model YAML config.

    Does not reuse config_loader._flatten() — it would incorrectly flatten
    the nested feature_defaults/model_hyperparameters dicts. Reuses
    config_loader.compute_config_hash() for hash bookkeeping consistency
    with the rest of the codebase's config pattern.
    """
    if yaml_path is None:
        candidates = [
            Path(__file__).resolve().parents[3] / 'configs' / 'pd_model_v1_config.yaml',
            Path('configs') / 'pd_model_v1_config.yaml',
        ]
        yaml_path = next((p for p in candidates if p.is_file()), candidates[0])

    path = Path(yaml_path)
    with open(path, 'r') as fh:
        raw = yaml.safe_load(fh)

    if not isinstance(raw, dict):
        raise PdModelConfigError(f'Expected a YAML mapping at top level, got {type(raw).__name__}')

    missing = _REQUIRED_PD_CONFIG_KEYS - raw.keys()
    if missing:
        raise PdModelConfigError(f'Missing required PD config keys: {sorted(missing)}')

    feature_names = set(raw['feature_names'])
    if set(raw['feature_defaults'].keys()) != feature_names:
        raise PdModelConfigError(
            'feature_defaults must define exactly the same features as feature_names '
            f'(feature_names={sorted(feature_names)}, '
            f'feature_defaults={sorted(raw["feature_defaults"].keys())})'
        )
    if set(raw['ensemble_weights'].keys()) != set(MODEL_NAMES):
        raise PdModelConfigError(
            f'ensemble_weights must define exactly {sorted(MODEL_NAMES)}, '
            f'got {sorted(raw["ensemble_weights"].keys())}'
        )

    config = dict(raw)
    config['_config_hash'] = compute_config_hash(config)
    config['_config_loaded_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return config


def score_pd_model_ensemble(layer1_df, models_dict, pd_config):
    """
    Scores layer1_df with each of the 4 fitted PD models and combines them
    into a weighted-average ensemble PD score.

    Returns a DataFrame:
      [feature_dt, subscriber_msisdn,
       pd_score_v1_model, pd_score_logreg_v1, pd_score_rf_v1,
       pd_score_xgb_v1, pd_score_lgbm_v1, pd_model_version]
    """
    feat_df = extract_pd_features(layer1_df, pd_config['feature_defaults'], pd_config['feature_names'])
    X = feat_df[pd_config['feature_names']]

    weights = pd_config['ensemble_weights']
    weight_total = sum(weights.values())

    out = pd.DataFrame({
        'feature_dt': layer1_df['feature_dt'].values,
        'subscriber_msisdn': layer1_df['subscriber_msisdn'].values,
    })

    ensemble_score = np.zeros(len(layer1_df))
    for model_name in MODEL_NAMES:
        model = models_dict[model_name]
        model_score = model.predict_proba(X)[:, 1]
        out[_SCORE_COLUMN_BY_MODEL[model_name]] = model_score
        ensemble_score += weights[model_name] * model_score

    out['pd_score_v1_model'] = np.clip(ensemble_score / weight_total, 1e-6, 1 - 1e-6)
    out['pd_model_version'] = pd_config['model_version']

    return out[[
        'feature_dt', 'subscriber_msisdn', 'pd_score_v1_model',
        'pd_score_logreg_v1', 'pd_score_rf_v1', 'pd_score_xgb_v1', 'pd_score_lgbm_v1',
        'pd_model_version',
    ]]
