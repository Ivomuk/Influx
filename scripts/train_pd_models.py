#!/usr/bin/env python3
"""
Entry point: (re)train the PD model ensemble (logistic regression, random
forest, XGBoost, LightGBM) and write artifacts to models/pd_v1/.

Usage:
    python scripts/train_pd_models.py

Trains against a synthetic, rule-consistent labeled dataset
(scoring.pd_training_data) since no real historical default outcomes exist
yet. Once monitoring.dim6_outcome_tracking.compute_realised_outcomes() has
accumulated enough real did_default_flag labels, swap the training_df source
below for real outcome data and rerun.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from telecom_credit_engine.scoring.pd_model import load_pd_model_config
from telecom_credit_engine.scoring.pd_model_training import train_all_pd_models, save_pd_models
from telecom_credit_engine.scoring.pd_training_data import generate_synthetic_pd_training_data

_DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), '..', 'models', 'pd_v1')

if __name__ == '__main__':
    pd_config = load_pd_model_config()
    training_df = generate_synthetic_pd_training_data(n_rows=2000, seed=42)

    print(f'Training PD model ensemble (version={pd_config["model_version"]}) '
          f'on {len(training_df)} synthetic rows...')
    models_dict, metrics_dict = train_all_pd_models(training_df, pd_config)

    for model_name, metrics in metrics_dict.items():
        print(f'  {model_name}: train_auc={metrics["train_auc"]:.4f} n_rows={metrics["n_rows"]}')

    metadata = save_pd_models(models_dict, metrics_dict, pd_config, _DEFAULT_OUTPUT_DIR)
    print(f'Saved PD model artifacts to {_DEFAULT_OUTPUT_DIR}')
    print(f'Metadata: {metadata}')
