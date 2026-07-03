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
from sklearn.model_selection import train_test_split
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


def train_all_pd_models(training_df, pd_config, label_col='did_default_flag',
                        holdout_fraction=0.25, holdout_seed=42):
    """
    Fits all 4 PD challenger models against training_df[label_col].

    Out-of-sample validation: a stratified holdout split is fit first to
    measure test discrimination (test_auc — the honest number), then each
    model is refit on the full dataset for the shipped artifact.

    Returns (models_dict, metrics_dict):
      models_dict:  {model_name: fitted_estimator (refit on full data)}
      metrics_dict: {model_name: {'train_auc', 'test_auc', 'n_rows',
                                  'n_test_rows', 'holdout_fraction'}}
    """
    feat_df = extract_pd_features(training_df, pd_config['feature_defaults'], pd_config['feature_names'])
    X = feat_df[pd_config['feature_names']]
    y = training_df[label_col].values

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=holdout_fraction, stratify=y, random_state=holdout_seed
    )

    hyperparams = pd_config['model_hyperparameters']

    models_dict = {}
    metrics_dict = {}
    for model_name in MODEL_NAMES:
        config_key = _HYPERPARAM_CONFIG_KEYS[model_name]
        fit_fn = _FIT_FUNCTIONS[model_name]
        model_hyperparams = hyperparams.get(config_key, {})

        holdout_model = fit_fn(X_train, y_train, model_hyperparams)
        train_auc = float(roc_auc_score(y_train, holdout_model.predict_proba(X_train)[:, 1]))
        test_auc = float(roc_auc_score(y_test, holdout_model.predict_proba(X_test)[:, 1]))

        # Ship the artifact refit on the full dataset; test_auc above is the
        # honest out-of-sample estimate for this hyperparameter configuration.
        models_dict[model_name] = fit_fn(X, y, model_hyperparams)
        metrics_dict[model_name] = {
            'train_auc': train_auc,
            'test_auc': test_auc,
            'n_rows': int(len(y)),
            'n_test_rows': int(len(y_test)),
            'holdout_fraction': holdout_fraction,
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
        'validation_methodology': 'stratified holdout for test_auc; shipped artifact refit on full data',
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
