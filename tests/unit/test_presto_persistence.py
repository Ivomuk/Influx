"""
Unit tests for presto_client.py and the orchestrator persistence functions.

All Presto connectivity is mocked — no live cluster required.
Tests verify:
  - _sql_literal formats all Python types correctly
  - PrestoQueryClient.insert_rows chunks correctly and builds valid SQL
  - PrestoQueryClient.query.to_dataframe returns a DataFrame from cursor rows
  - persist_cross_cycle_state writes to all three Hive tables with correct data
  - load_cross_cycle_state restores globals from mocked query results
"""
import json
import types
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from telecom_credit_engine.orchestration.presto_client import (
    PrestoQueryClient,
    _sql_literal,
)


# ---------------------------------------------------------------------------
# _sql_literal
# ---------------------------------------------------------------------------

def test_sql_literal_none():
    assert _sql_literal(None) == 'NULL'

def test_sql_literal_true():
    assert _sql_literal(True) == 'TRUE'

def test_sql_literal_false():
    assert _sql_literal(False) == 'FALSE'

def test_sql_literal_int():
    assert _sql_literal(42) == '42'

def test_sql_literal_float():
    assert _sql_literal(1.5) == '1.5'

def test_sql_literal_string():
    assert _sql_literal('hello') == "'hello'"

def test_sql_literal_string_escapes_single_quote():
    assert _sql_literal("it's") == "'it''s'"

def test_sql_literal_timestamp():
    ts = pd.Timestamp('2026-04-01 12:30:00')
    assert _sql_literal(ts) == "TIMESTAMP '2026-04-01 12:30:00'"


# ---------------------------------------------------------------------------
# PrestoQueryClient helpers
# ---------------------------------------------------------------------------

def _make_client():
    """Return a PrestoQueryClient wired to a mock connection."""
    conn = MagicMock()
    return PrestoQueryClient(conn), conn


# ---------------------------------------------------------------------------
# insert_rows
# ---------------------------------------------------------------------------

def test_insert_rows_single_chunk():
    client, conn = _make_client()
    client.insert_rows(
        'credit_engine.pipeline_run_metadata',
        ['operating_mode', 'run_date'],
        [('NORMAL', '2026-04-01')],
    )
    cursor = conn.cursor.return_value
    executed_sql = cursor.execute.call_args[0][0]
    assert 'INSERT INTO credit_engine.pipeline_run_metadata' in executed_sql
    assert "'NORMAL'" in executed_sql
    assert "'2026-04-01'" in executed_sql


def test_insert_rows_chunks_correctly():
    client, conn = _make_client()
    rows = [(f'msisdn_{i}', float(i * 1000), '2026-04-01') for i in range(12)]
    client.insert_rows(
        'credit_engine.pipeline_capacity_state',
        ['subscriber_msisdn', 'credit_limit', 'run_date'],
        rows,
        chunk_size=5,
    )
    # 12 rows at chunk_size=5 → 3 INSERT statements
    assert conn.cursor.return_value.execute.call_count == 3


def test_insert_rows_empty_list_produces_no_inserts():
    client, conn = _make_client()
    client.insert_rows('some.table', ['col'], [])
    conn.cursor.return_value.execute.assert_not_called()


def test_insert_rows_boolean_literal():
    client, conn = _make_client()
    client.insert_rows('t', ['flag', 'run_date'], [(True, '2026-04-01')])
    sql = conn.cursor.return_value.execute.call_args[0][0]
    assert 'TRUE' in sql


def test_insert_rows_null_literal():
    client, conn = _make_client()
    client.insert_rows('t', ['col', 'run_date'], [(None, '2026-04-01')])
    sql = conn.cursor.return_value.execute.call_args[0][0]
    assert 'NULL' in sql


# ---------------------------------------------------------------------------
# query / to_dataframe
# ---------------------------------------------------------------------------

def test_to_dataframe_returns_correct_columns_and_rows():
    client, conn = _make_client()
    cursor = conn.cursor.return_value
    cursor.description = [('subscriber_msisdn',), ('credit_limit',)]
    cursor.fetchall.return_value = [('256700000001', 10000.0), ('256700000002', 5000.0)]

    df = client.query('SELECT * FROM t').to_dataframe()

    assert list(df.columns) == ['subscriber_msisdn', 'credit_limit']
    assert len(df) == 2
    assert df.iloc[0]['credit_limit'] == 10000.0


def test_to_dataframe_empty_result_returns_empty_df():
    client, conn = _make_client()
    cursor = conn.cursor.return_value
    cursor.description = [('col',)]
    cursor.fetchall.return_value = []

    df = client.query('SELECT * FROM t WHERE 1=0').to_dataframe()

    assert df.empty
    assert 'col' in df.columns


def test_to_dataframe_none_description_returns_empty_df():
    client, conn = _make_client()
    cursor = conn.cursor.return_value
    cursor.description = None
    cursor.fetchall.return_value = []

    df = client.query('DDL statement').to_dataframe()

    assert df.empty


# ---------------------------------------------------------------------------
# persist_cross_cycle_state
# ---------------------------------------------------------------------------

def _make_mock_query_client():
    qc = MagicMock()
    qc.insert_rows = MagicMock()
    return qc


def _patch_globals(**overrides):
    """Return a dict of orchestrator globals to patch."""
    defaults = {
        '_previous_capacity_df': pd.DataFrame({
            'subscriber_msisdn': ['256700000001'],
            'CreditLimit': [10000.0],
        }),
        '_prior_state_df': pd.DataFrame({
            'subscriber_msisdn': ['256700000001'],
            'operating_state': ['healthy'],
            'state_change_dt': [pd.Timestamp('2026-04-01')],
        }),
        '_circuit_breaker_multiplier': 0.80,
        '_current_operating_mode': 'CONSERVATIVE',
        '_last_certification_report': {
            'certified': True,
            'passed': 10,
            'failed': 0,
            'errors': 0,
            'pipeline_run_id': 'batch_2026-04-01',
            'execution_dt': '2026-04-01T02:00:00+00:00',
            'total_checks': 10,
            'details': 'skipped in JSON',
        },
    }
    defaults.update(overrides)
    return defaults


def test_persist_writes_capacity_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_capacity_state' in tables_written


def test_persist_writes_subscriber_state_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_subscriber_state' in tables_written


def test_persist_writes_run_metadata_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_run_metadata' in tables_written


def test_persist_metadata_row_contains_cb_multiplier_and_mode():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    meta_call = next(
        c for c in qc.insert_rows.call_args_list
        if c[0][0] == 'credit_engine.pipeline_run_metadata'
    )
    row = meta_call[0][2][0]   # first (only) row tuple
    assert row[0] == 0.80                # circuit_breaker_multiplier
    assert row[1] == 'CONSERVATIVE'     # operating_mode
    assert row[2] is True               # certification_passed


def test_persist_skips_capacity_write_when_none():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals(_previous_capacity_df=None)):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_capacity_state' not in tables_written


def test_persist_skips_state_write_when_none():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_query_client()
    with patch.multiple(orch, **_patch_globals(_prior_state_df=None)):
        orch.persist_cross_cycle_state('2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_subscriber_state' not in tables_written


# ---------------------------------------------------------------------------
# load_cross_cycle_state
# ---------------------------------------------------------------------------

def _make_load_client(cap_rows, state_rows, meta_rows):
    """Return a mock query_client whose .query().to_dataframe() returns preset data."""
    qc = MagicMock()

    def _query(sql):
        result = MagicMock()
        if 'pipeline_capacity_state' in sql:
            result.to_dataframe.return_value = pd.DataFrame(
                cap_rows, columns=['subscriber_msisdn', 'credit_limit']
            )
        elif 'pipeline_subscriber_state' in sql:
            result.to_dataframe.return_value = pd.DataFrame(
                state_rows, columns=['subscriber_msisdn', 'operating_state', 'state_change_dt']
            )
        elif 'pipeline_run_metadata' in sql:
            result.to_dataframe.return_value = pd.DataFrame(
                meta_rows, columns=['circuit_breaker_multiplier', 'operating_mode']
            )
        else:
            result.to_dataframe.return_value = pd.DataFrame()
        return result

    qc.query.side_effect = _query
    return qc


def test_load_restores_previous_capacity_df():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(
        cap_rows=[('256700000001', 10000.0)],
        state_rows=[('256700000001', 'healthy', pd.Timestamp('2026-04-01'))],
        meta_rows=[(0.80, 'CONSERVATIVE')],
    )
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
        assert orch._previous_capacity_df is not None
        assert 'CreditLimit' in orch._previous_capacity_df.columns


def test_load_restores_circuit_breaker_and_mode():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(
        cap_rows=[('256700000001', 10000.0)],
        state_rows=[('256700000001', 'healthy', pd.Timestamp('2026-04-01'))],
        meta_rows=[(0.70, 'MANUAL_REVIEW_ONLY')],
    )
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
        assert orch._circuit_breaker_multiplier == 0.70
        assert orch._current_operating_mode == 'MANUAL_REVIEW_ONLY'


def test_load_handles_empty_tables_gracefully():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(cap_rows=[], state_rows=[], meta_rows=[])
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
        # Globals should remain at their defaults when tables are empty
        assert orch._previous_capacity_df is None
        assert orch._circuit_breaker_multiplier == 1.0
        assert orch._current_operating_mode == 'NORMAL'


def test_load_parses_state_change_dt_as_timestamp():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(
        cap_rows=[],
        state_rows=[('256700000001', 'healthy', '2026-04-01 00:00:00')],
        meta_rows=[],
    )
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
        assert pd.api.types.is_datetime64_any_dtype(
            orch._prior_state_df['state_change_dt']
        )
