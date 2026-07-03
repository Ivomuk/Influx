# PD model training — fits the 4 challenger models (logistic regression,
# random forest, XGBoost, LightGBM) and persists them as joblib artifacts.
#
# This is an offline/interactive entry point (see scripts/train_pd_models.py)
# — it is NOT called by the automated daily batch pipeline. Retrain once
# real did_default_flag outcomes accumulate via
# monitoring.dim6_outcome_tracking.compute_realised_outcomes().

import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier

from telecom_credit_engine.scoring.pd_features import extract_pd_features

# Maps the short model key (used in file names / score columns) to the
# config's model_hyperparameters section name.
MODEL_NAMES = ('logreg', 'rf', 'xgb', 'lgbm')
_HYPERPARAM_CONFIG_KEYS = {
    'logreg': 'logistic_regression',
    'rf': 'random_forest',
    'xgb': 'xgboost',
    'lgbm': 'lightgbm',
}


class PdModelArtifactError(Exception):
    pass


def fit_logistic_regression(X, y, hyperparams):
    model = LogisticRegression(**hyperparams)
    model.fit(X, y)
    return model


def fit_random_forest(X, y, hyperparams):
    model = RandomForestClassifier(**hyperparams)
    model.fit(X, y)
    return model


def fit_xgboost(X, y, hyperparams):
    model = XGBClassifier(**hyperparams)
    model.fit(X, y)
    return model


def fit_lightgbm(X, y, hyperparams):
    model = LGBMClassifier(**hyperparams)
    model.fit(X, y)
    return model


_FIT_FUNCTIONS = {
    'logreg': fit_logistic_regression,
    'rf': fit_random_forest,
    'xgb': fit_xgboost,
    'lgbm': fit_lightgbm,
}


def train_all_pd_models(training_df, pd_config, label_col='did_default_flag'):
    """
    Fits all 4 PD challenger models against training_df[label_col].

    Returns (models_dict, metrics_dict):
      models_dict:  {model_name: fitted_estimator}
      metrics_dict: {model_name: {'train_auc': float, 'n_rows': int}}
    """
    feat_df = extract_pd_features(training_df, pd_config['feature_defaults'], pd_config['feature_names'])
    X = feat_df[pd_config['feature_names']]
    y = training_df[label_col].values

    hyperparams = pd_config['model_hyperparameters']

    models_dict = {}
    metrics_dict = {}
    for model_name in MODEL_NAMES:
        config_key = _HYPERPARAM_CONFIG_KEYS[model_name]
        fit_fn = _FIT_FUNCTIONS[model_name]
        model = fit_fn(X, y, hyperparams.get(config_key, {}))
        predicted_probability = model.predict_proba(X)[:, 1]
        models_dict[model_name] = model
        metrics_dict[model_name] = {
            'train_auc': float(roc_auc_score(y, predicted_probability)),
            'n_rows': int(len(y)),
        }

    return models_dict, metrics_dict


def save_pd_models(models_dict, metrics_dict, pd_config, output_dir, synthetic_training_data=True):
    """Persists each fitted model as a joblib artifact plus a metadata.json summary."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    for model_name, model in models_dict.items():
        joblib.dump(model, out_path / f'{model_name}.joblib')

    metadata = {
        'model_version': pd_config['model_version'],
        'trained_at': datetime.now(timezone.utc).isoformat(),
        'feature_names': pd_config['feature_names'],
        'metrics': metrics_dict,
        'synthetic_training_data': synthetic_training_data,
    }
    with open(out_path / 'metadata.json', 'w') as fh:
        json.dump(metadata, fh, indent=2)

    return metadata


def load_pd_models(model_dir):
    """Loads all 4 model artifacts. Raises PdModelArtifactError if any is missing."""
    model_path = Path(model_dir)
    models_dict = {}
    missing = []
    for model_name in MODEL_NAMES:
        artifact_path = model_path / f'{model_name}.joblib'
        if not artifact_path.is_file():
            missing.append(str(artifact_path))
            continue
        models_dict[model_name] = joblib.load(artifact_path)

    if missing:
        raise PdModelArtifactError(
            f'Missing PD model artifact(s): {missing}. '
            f'Run scripts/train_pd_models.py to generate them.'
        )

    return models_dict
