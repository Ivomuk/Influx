#!/usr/bin/env python3
"""
Entry point: replay decisions from a stored snapshot and diff against originals.

Usage:
    python scripts/run_replay.py <snapshot_path.parquet> [--config <config_path>]

Loads a decision snapshot (output of store_decision_snapshot), re-runs the
decision engine, and prints a diff of any changed CreditLimit, action, status,
or reason code. Use --config to supply an alternative config dict for
what-if / impact analysis.

Exits 0 if no differences (deterministic), 1 if any rows changed.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from telecom_credit_engine.governance.decision_replay import (
    replay_decisions, diff_decisions, detect_nondeterminism
)

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python run_replay.py <snapshot_path.parquet>')
        sys.exit(1)

    import pandas as pd
    snapshot_path = sys.argv[1]
    snapshot_df = pd.read_parquet(snapshot_path)

    replayed_df = replay_decisions(snapshot_df)
    diff_df = diff_decisions(snapshot_df, replayed_df)

    if diff_df.empty:
        print('Replay matched original decisions exactly.')
        sys.exit(0)
    else:
        print(f'{len(diff_df)} decisions changed:')
        print(diff_df.to_string(index=False))
        sys.exit(1)
