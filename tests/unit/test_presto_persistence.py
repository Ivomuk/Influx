"""
Unit tests for presto_client.py and the orchestrator persistence functions.

All Presto connectivity is mocked — no live cluster required.
Tests verify:
  - _sql_literal formats all Python types correctly
  - PrestoQueryClient.insert_rows chunks correctly and builds valid SQL
  - PrestoQueryClient.query.to_dataframe returns a DataFrame from cursor rows
  - persist_cross_cycle_state: idempotency skip, all table writes, commit log last
  - persist_cross_cycle_state: state validation raises on unknown operating_state
  - persist_cross_cycle_state: retry exhaustion raises RuntimeError
  - load_cross_cycle_state restores globals from mocked query results
  - load_cross_cycle_state: validates loaded states; raises on unknown values
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


def test_insert_rows_retries_then_raises_on_exhaustion():
    """After max_retries failures on a chunk, RuntimeError is raised."""
    client, conn = _make_client()
    conn.cursor.return_value.execute.side_effect = Exception('Presto timeout')
    with pytest.raises(RuntimeError, match='failed after'):
        client.insert_rows('t', ['col'], [('val',)], max_retries=2)


def test_insert_rows_succeeds_on_second_attempt():
    """insert_rows returns normally when second attempt succeeds."""
    client, conn = _make_client()
    # First call raises; second call succeeds (returns None by default)
    conn.cursor.return_value.execute.side_effect = [Exception('transient'), None]
    client.insert_rows('t', ['col'], [('val',)], max_retries=3)
    assert conn.cursor.return_value.execute.call_count == 2


def test_insert_rows_overwrite_sets_session_before_insert():
    """With overwrite=True, SET SESSION OVERWRITE is the first execute call."""
    client, conn = _make_client()
    client.insert_rows('t', ['col'], [('val',)], overwrite=True)
    all_sqls = [c[0][0] for c in conn.cursor.return_value.execute.call_args_list]
    assert any("OVERWRITE" in s for s in all_sqls), "SET SESSION OVERWRITE not found"
    overwrite_idx = next(i for i, s in enumerate(all_sqls) if 'OVERWRITE' in s)
    insert_idx = next(i for i, s in enumerate(all_sqls) if s.startswith('INSERT'))
    assert overwrite_idx < insert_idx, "SET SESSION OVERWRITE must precede INSERT"


def test_insert_rows_overwrite_resets_session_after_first_chunk():
    """After the first chunk, SET SESSION APPEND is called; second chunk gets no OVERWRITE."""
    client, conn = _make_client()
    rows = [('a',), ('b',), ('c',)]
    client.insert_rows('t', ['col'], rows, chunk_size=2, overwrite=True)
    all_sqls = [c[0][0] for c in conn.cursor.return_value.execute.call_args_list]
    overwrite_calls = [s for s in all_sqls if 'OVERWRITE' in s]
    append_calls = [s for s in all_sqls if 'APPEND' in s]
    insert_calls = [s for s in all_sqls if s.startswith('INSERT')]
    assert len(overwrite_calls) == 1, "SET SESSION OVERWRITE should appear exactly once"
    assert len(append_calls) >= 1,    "SET SESSION APPEND should appear at least once"
    assert len(insert_calls) == 2,    "Two chunks → two INSERT statements"
    # OVERWRITE must precede the first INSERT; APPEND must follow the first INSERT
    ow_idx = all_sqls.index(overwrite_calls[0])
    first_insert_idx = next(i for i, s in enumerate(all_sqls) if s.startswith('INSERT'))
    ap_idx = next(i for i, s in enumerate(all_sqls) if 'APPEND' in s)
    assert ow_idx < first_insert_idx < ap_idx


def test_insert_rows_overwrite_false_no_set_session_calls():
    """With overwrite=False (default), no SET SESSION statements are issued."""
    client, conn = _make_client()
    client.insert_rows('t', ['col'], [('val',)])
    all_sqls = [c[0][0] for c in conn.cursor.return_value.execute.call_args_list]
    assert not any('SET SESSION' in s for s in all_sqls)


def test_insert_rows_overwrite_warns_on_reset_failure():
    """If the SET SESSION APPEND reset fails, a RuntimeWarning is emitted."""
    import warnings as _warnings
    client, conn = _make_client()
    execute_calls = []

    def _side_effect(sql):
        execute_calls.append(sql)
        if 'APPEND' in sql:
            raise Exception('Presto session reset error')

    conn.cursor.return_value.execute.side_effect = _side_effect

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter('always')
        client.insert_rows('t', ['col'], [('val',)], overwrite=True)

    runtime_warnings = [w for w in caught if issubclass(w.category, RuntimeWarning)]
    assert len(runtime_warnings) == 1
    assert 'OVERWRITE mode' in str(runtime_warnings[0].message)


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
# persist_cross_cycle_state — shared helpers
# ---------------------------------------------------------------------------

def _make_mock_persist_client(already_committed=False, reconcile_overrides=None):
    """
    Return a mock query_client suitable for persist tests.

    already_committed    : if True, the idempotency COUNT(*) on commit_log returns 1
                           so persist skips the entire write.
    reconcile_overrides  : dict mapping a table-name substring to the COUNT(*) value
                           to return for that table's reconciliation query.
                           e.g. {'pipeline_subscriber_state': 0} returns 0 for the
                           subscriber-state reconciliation and 1 for all others.
                           Omit to have all reconciliation queries return the
                           matching expected count (tests pass cleanly).
    """
    overrides = reconcile_overrides or {}
    qc = MagicMock()
    qc.insert_rows = MagicMock()

    def _query(sql):
        result = MagicMock()
        if 'COUNT(*)' in sql or 'count(*)' in sql.lower():
            if 'pipeline_commit_log' in sql:
                # Idempotency check
                count = 1 if already_committed else 0
            else:
                # Reconciliation queries — check for per-table override
                count = 1
                for table_fragment, override_count in overrides.items():
                    if table_fragment in sql:
                        count = override_count
                        break
            result.to_dataframe.return_value = pd.DataFrame([{'cnt': count}])
        else:
            result.to_dataframe.return_value = pd.DataFrame()
        return result

    qc.query.side_effect = _query
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


# ---------------------------------------------------------------------------
# persist_cross_cycle_state — table write tests
# ---------------------------------------------------------------------------

def test_persist_writes_capacity_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_capacity_state' in tables_written


def test_persist_writes_subscriber_state_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_subscriber_state' in tables_written


def test_persist_writes_run_metadata_table():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_run_metadata' in tables_written


def test_persist_writes_commit_log_last():
    """pipeline_commit_log must be the final insert_rows call."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert tables_written[-1] == 'credit_engine.pipeline_commit_log'


def test_persist_metadata_row_contains_cb_multiplier_and_mode():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    meta_call = next(
        c for c in qc.insert_rows.call_args_list
        if c[0][0] == 'credit_engine.pipeline_run_metadata'
    )
    row = meta_call[0][2][0]   # first (only) row tuple
    assert row[0] == 0.80                # circuit_breaker_multiplier
    assert row[1] == 'CONSERVATIVE'     # operating_mode
    assert row[2] is True               # certification_passed


def test_persist_data_tables_use_overwrite_true():
    """The three data tables must be written with overwrite=True; commit log must not."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    data_tables = {
        'credit_engine.pipeline_capacity_state',
        'credit_engine.pipeline_subscriber_state',
        'credit_engine.pipeline_run_metadata',
    }
    for c in qc.insert_rows.call_args_list:
        table = c[0][0]
        overwrite_flag = c[1].get('overwrite', False)
        if table in data_tables:
            assert overwrite_flag is True, f'{table} must be written with overwrite=True'
        elif table == 'credit_engine.pipeline_commit_log':
            assert overwrite_flag is False or 'overwrite' not in c[1], \
                'commit log must use default overwrite=False'


def test_persist_skips_capacity_write_when_none():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals(_previous_capacity_df=None)):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_capacity_state' not in tables_written


def test_persist_skips_state_write_when_none():
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals(_prior_state_df=None)):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    tables_written = [c[0][0] for c in qc.insert_rows.call_args_list]
    assert 'credit_engine.pipeline_subscriber_state' not in tables_written


# ---------------------------------------------------------------------------
# persist_cross_cycle_state — idempotency
# ---------------------------------------------------------------------------

def test_persist_skips_all_writes_when_already_committed():
    """If commit log already has this run_id as COMMITTED, no insert_rows called."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client(already_committed=True)
    with patch.multiple(orch, **_patch_globals()):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)

    qc.insert_rows.assert_not_called()


# ---------------------------------------------------------------------------
# persist_cross_cycle_state — state validation
# ---------------------------------------------------------------------------

def test_persist_raises_on_unknown_operating_state():
    """persist should raise ValueError if prior_state_df contains an unknown state."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    bad_state_df = pd.DataFrame({
        'subscriber_msisdn': ['256700000001'],
        'operating_state': ['UNKNOWN_STATE'],
        'state_change_dt': [pd.Timestamp('2026-04-01')],
    })
    qc = _make_mock_persist_client()
    with patch.multiple(orch, **_patch_globals(_prior_state_df=bad_state_df)):
        with pytest.raises(ValueError, match='unknown operating_state'):
            orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)


def test_persist_accepts_all_valid_operating_states():
    """All six valid states must not raise."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    valid_states = ['healthy', 'at_risk', 'distressed', 'recovered', 'cooling', 'fraud_review']
    state_df = pd.DataFrame({
        'subscriber_msisdn': [f'256700000{i:03d}' for i in range(len(valid_states))],
        'operating_state': valid_states,
        'state_change_dt': [pd.Timestamp('2026-04-01')] * len(valid_states),
    })
    # subscriber_state reconciliation expects 6 rows (one per valid state)
    qc = _make_mock_persist_client(
        reconcile_overrides={'pipeline_subscriber_state': len(valid_states)}
    )
    with patch.multiple(orch, **_patch_globals(_prior_state_df=state_df)):
        orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)


# ---------------------------------------------------------------------------
# persist_cross_cycle_state — reconciliation failures
# ---------------------------------------------------------------------------

def test_persist_raises_on_subscriber_state_reconciliation_mismatch():
    """RuntimeError raised when Hive COUNT(*) for subscriber_state doesn't match expected."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client(
        reconcile_overrides={'pipeline_subscriber_state': 0}
    )
    with patch.multiple(orch, **_patch_globals()):
        with pytest.raises(RuntimeError, match='Subscriber state reconciliation failed'):
            orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)


def test_persist_raises_on_run_metadata_reconciliation_mismatch():
    """RuntimeError raised when Hive COUNT(*) for run_metadata doesn't equal 1."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_mock_persist_client(
        reconcile_overrides={'pipeline_run_metadata': 0}
    )
    with patch.multiple(orch, **_patch_globals()):
        with pytest.raises(RuntimeError, match='Run metadata reconciliation failed'):
            orch.persist_cross_cycle_state('2026-04-01', 'batch_2026-04-01', qc)


# ---------------------------------------------------------------------------
# load_cross_cycle_state
# ---------------------------------------------------------------------------

def _make_load_client(cap_rows, state_rows, meta_rows, last_committed_date='2026-04-01'):
    """Return a mock query_client whose .query().to_dataframe() returns preset data."""
    qc = MagicMock()

    def _query(sql):
        result = MagicMock()
        if 'pipeline_commit_log' in sql:
            if last_committed_date:
                result.to_dataframe.return_value = pd.DataFrame(
                    [{'last_committed_date': last_committed_date}]
                )
            else:
                result.to_dataframe.return_value = pd.DataFrame(columns=['last_committed_date'])
        elif 'pipeline_capacity_state' in sql:
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


def test_load_handles_no_committed_state_gracefully():
    """If commit log is empty (no committed runs), globals stay at defaults."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(
        cap_rows=[], state_rows=[], meta_rows=[],
        last_committed_date=None,
    )
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
        assert orch._previous_capacity_df is None
        assert orch._circuit_breaker_multiplier == 1.0
        assert orch._current_operating_mode == 'NORMAL'


def test_load_handles_empty_data_tables_gracefully():
    """Committed date found but data tables are empty — globals stay at defaults."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(cap_rows=[], state_rows=[], meta_rows=[])
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        orch.load_cross_cycle_state(qc)
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


def test_load_raises_on_unknown_operating_state_in_hive():
    """load_cross_cycle_state must raise ValueError if Hive returns an unknown state."""
    import telecom_credit_engine.orchestration.orchestrator as orch
    qc = _make_load_client(
        cap_rows=[],
        state_rows=[('256700000001', 'CORRUPTED_STATE', '2026-04-01 00:00:00')],
        meta_rows=[],
    )
    with patch.multiple(orch,
                        _previous_capacity_df=None,
                        _prior_state_df=None,
                        _circuit_breaker_multiplier=1.0,
                        _current_operating_mode='NORMAL'):
        with pytest.raises(ValueError, match='unknown operating_state'):
            orch.load_cross_cycle_state(qc)
