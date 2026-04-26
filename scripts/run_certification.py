#!/usr/bin/env python3
"""
Entry point: run the full certification pipeline standalone.

Usage:
    python scripts/run_certification.py <execution_date YYYY-MM-DD>

Runs all SQL QA suites (vw1–vw4) via bq_client and all Python QA suites
(decision engine, API output contract). Exits 0 if certified, 1 if any
check fails.

Wire up bq_client and sql_qa_*_checks dicts from your environment before use.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from telecom_credit_engine.orchestration.run_certification import (
    run_certification, assert_certified, CertificationFailedError
)

if __name__ == '__main__':
    # Replace these stubs with real inputs from your pipeline context.
    raise NotImplementedError(
        'Wire up bq_client, sql_qa_*_checks dicts, engine_outputs, '
        'eligibility_df, and state_df before running this script.'
    )
