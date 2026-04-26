#!/usr/bin/env python3
"""
Entry point: run the daily batch pipeline for a given execution date.

Usage:
    python scripts/run_batch_pipeline.py 2026-04-26

The pipeline runs vw1→vw6 SQL views, the Python decision engine (Layers 2–4),
Layers 5–8 state/portfolio/governance, feature store bootstrap, and outcome
tracking. Cross-cycle state is persisted between runs.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from telecom_credit_engine.orchestration.orchestrator import run_batch_pipeline

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python run_batch_pipeline.py <execution_date YYYY-MM-DD>')
        sys.exit(1)
    execution_date = sys.argv[1]
    result = run_batch_pipeline(execution_date=execution_date, lender_feedback_df=None)
    certified = result.get('certification_report', {}).get('certified', False)
    sys.exit(0 if certified else 1)
