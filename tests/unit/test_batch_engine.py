"""Unit tests for PrestoSQLBatchEngine and run_sql_batch_pipeline."""
import warnings
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(sql_dir, table_exists=False, execute_side_effect=None):
    """Return a PrestoSQLBatchEngine backed by a mock query client."""
    from telecom_credit_engine.orchestration.batch_engine import PrestoSQLBatchEngine

    qc = MagicMock()

    # _table_exists uses query().to_dataframe()
    table_exists_df = pd.DataFrame([{'cnt': 1 if table_exists else 0}])
    qc.query.return_value.to_dataframe.return_value = table_exists_df

    if execute_side_effect is not None:
        qc.execute.side_effect = execute_side_effect

    return PrestoSQLBatchEngine(qc, sql_dir=str(sql_dir)), qc


def _write_sql(sql_dir, view_name, content='CREATE OR REPLACE VIEW placeholder AS SELECT 1'):
    path = sql_dir / f'{view_name}.sql'
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_run_vw1_materialises_into_same_named_table(tmp_path):
    sql_content = 'CREATE OR REPLACE VIEW hive.credit_engine.vw1 AS SELECT 1 AS x'
    _write_sql(tmp_path, 'vw1_credit_v1_normalized_events', sql_content)
    engine, qc = _make_engine(tmp_path, table_exists=False)

    engine.run('vw1_credit_v1_normalized_events', '2025-01-15')

    executed_sql = qc.execute.call_args[0][0]
    assert 'CREATE TABLE hive.credit_engine.vw1_credit_v1_normalized_events' in executed_sql
    assert "'2025-01-15' AS run_date" in executed_sql
    assert "WHERE t.event_dt = DATE '2025-01-15'" in executed_sql
    assert 'SELECT 1 AS x' in executed_sql


def test_run_vw2_materialises_into_same_named_table(tmp_path):
    sql_content = 'CREATE OR REPLACE VIEW hive.credit_engine.vw2 AS SELECT 2 AS y'
    _write_sql(tmp_path, 'vw2_credit_v1_subscriber_day', sql_content)
    engine, qc = _make_engine(tmp_path, table_exists=False)

    engine.run('vw2_credit_v1_subscriber_day', '2025-01-15')

    executed_sql = qc.execute.call_args[0][0]
    assert 'CREATE TABLE hive.credit_engine.vw2_credit_v1_subscriber_day' in executed_sql
    assert "'2025-01-15' AS run_date" in executed_sql
    assert "WHERE t.event_dt = DATE '2025-01-15'" in executed_sql
    assert 'SELECT 2 AS y' in executed_sql


def test_run_vw3_ctas_when_table_absent(tmp_path):
    _write_sql(tmp_path, 'vw3_credit_v1_layer1_features')
    engine, qc = _make_engine(tmp_path, table_exists=False)

    engine.run('vw3_credit_v1_layer1_features', '2025-01-15')

    executed_sql = qc.execute.call_args[0][0]
    assert 'CREATE TABLE hive.credit_engine.vw3_credit_v1_layer1_features' in executed_sql
    assert "WITH (format = 'PARQUET'" in executed_sql
    assert "'2025-01-15' AS run_date" in executed_sql
    assert "WHERE t.feature_dt = DATE '2025-01-15'" in executed_sql


def test_run_vw3_insert_overwrite_when_table_exists(tmp_path):
    _write_sql(tmp_path, 'vw3_credit_v1_layer1_features')
    engine, qc = _make_engine(tmp_path, table_exists=True)

    engine.run('vw3_credit_v1_layer1_features', '2025-01-15')

    calls = [c[0][0] for c in qc.execute.call_args_list]
    overwrite_set = next(
        (s for s in calls if 'insert_existing_partitions_behavior' in s and 'OVERWRITE' in s),
        None,
    )
    insert_sql = next((s for s in calls if s.startswith('INSERT INTO')), None)
    assert overwrite_set is not None, 'Expected SET SESSION OVERWRITE call'
    assert insert_sql is not None, 'Expected INSERT INTO call'
    assert 'vw3_credit_v1_layer1_features' in insert_sql
    assert "WHERE t.feature_dt = DATE '2025-01-15'" in insert_sql


def test_run_insert_overwrite_resets_session_in_finally(tmp_path):
    _write_sql(tmp_path, 'vw3_credit_v1_layer1_features')
    engine, qc = _make_engine(tmp_path, table_exists=True)

    engine.run('vw3_credit_v1_layer1_features', '2025-01-15')

    calls = [c[0][0] for c in qc.execute.call_args_list]
    append_reset = next(
        (s for s in calls if 'insert_existing_partitions_behavior' in s and 'APPEND' in s),
        None,
    )
    assert append_reset is not None, 'Expected SET SESSION APPEND reset in finally'


def test_run_retries_on_failure_then_raises(tmp_path):
    _write_sql(tmp_path, 'vw3_credit_v1_layer1_features')
    engine, qc = _make_engine(tmp_path, table_exists=False)
    qc.execute.side_effect = Exception('Presto connection error')

    with patch('time.sleep'):
        with pytest.raises(RuntimeError, match='batch_engine: materialise.*failed after 3 attempts'):
            engine.run('vw3_credit_v1_layer1_features', '2025-01-15', max_retries=3)

    assert qc.execute.call_count == 3


def test_read_queries_mat_table_with_correct_date(tmp_path):
    from telecom_credit_engine.orchestration.batch_engine import PrestoSQLBatchEngine

    qc = MagicMock()
    expected_df = pd.DataFrame([{'subscriber_msisdn': '123', 'feature_dt': '2025-01-15'}])
    qc.query.return_value.to_dataframe.return_value = expected_df
    engine = PrestoSQLBatchEngine(qc, sql_dir=str(tmp_path))

    result = engine.read('vw3_credit_v1_layer1_features', '2025-01-15')

    query_sql = qc.query.call_args[0][0]
    assert 'vw3_credit_v1_layer1_features' in query_sql
    assert "run_date = '2025-01-15'" in query_sql
    pd.testing.assert_frame_equal(result, expected_df)


def test_run_sql_batch_pipeline_returns_four_dataframes(tmp_path):
    from telecom_credit_engine.orchestration.orchestrator import run_sql_batch_pipeline

    # Write stub SQL files for vw1 and vw2 (non-materialised views)
    for vw in ['vw1_credit_v1_normalized_events', 'vw2_credit_v1_subscriber_day']:
        _write_sql(tmp_path, vw)

    mock_engine = MagicMock()
    dfs = {
        'vw3_credit_v1_layer1_features': pd.DataFrame([{'a': 1}]),
        'vw4_credit_v1_layer0_scores':   pd.DataFrame([{'b': 2}]),
        'vw5_credit_v1_reason_codes':    pd.DataFrame([{'c': 3}]),
        'vw6_credit_v1_cap_and_action':  pd.DataFrame([{'d': 4}]),
    }
    mock_engine.read.side_effect = lambda vn, _dt: dfs[vn]

    layer1_df, layer0_df, vw5_df, vw6_df = run_sql_batch_pipeline('2025-01-15', mock_engine)

    assert mock_engine.run.call_count == 6
    pd.testing.assert_frame_equal(layer1_df, dfs['vw3_credit_v1_layer1_features'])
    pd.testing.assert_frame_equal(layer0_df, dfs['vw4_credit_v1_layer0_scores'])
    pd.testing.assert_frame_equal(vw5_df,    dfs['vw5_credit_v1_reason_codes'])
    pd.testing.assert_frame_equal(vw6_df,    dfs['vw6_credit_v1_cap_and_action'])
