import logging
import re
import warnings
from pathlib import Path

_log = logging.getLogger(__name__)

# All 6 views are materialised into same-named Hive tables.
# Environments that do not support CREATE VIEW use these tables in place of
# lazy Presto views; the dependency chain (vw3 references vw2, etc.) is
# preserved because the table names match the former view names.
_MATERIALISE = {
    'vw1_credit_v1_normalized_events',
    'vw2_credit_v1_subscriber_day',
    'vw3_credit_v1_layer1_features',
    'vw4_credit_v1_layer0_scores',
    'vw5_credit_v1_reason_codes',
    'vw6_credit_v1_cap_and_action',
}

_DATE_COL = {
    'vw1_credit_v1_normalized_events': 'event_dt',
    'vw2_credit_v1_subscriber_day':    'event_dt',
    'vw3_credit_v1_layer1_features':   'feature_dt',
    'vw4_credit_v1_layer0_scores':     'feature_dt',
    'vw5_credit_v1_reason_codes':      'feature_dt',
    'vw6_credit_v1_cap_and_action':    'feature_dt',
}

# Target table name == view name (identity mapping).
# Tables are pre-created by deployment/pipeline_view_tables.sql.
_MAT_SUFFIX = {
    'vw1_credit_v1_normalized_events': 'vw1_credit_v1_normalized_events',
    'vw2_credit_v1_subscriber_day':    'vw2_credit_v1_subscriber_day',
    'vw3_credit_v1_layer1_features':   'vw3_credit_v1_layer1_features',
    'vw4_credit_v1_layer0_scores':     'vw4_credit_v1_layer0_scores',
    'vw5_credit_v1_reason_codes':      'vw5_credit_v1_reason_codes',
    'vw6_credit_v1_cap_and_action':    'vw6_credit_v1_cap_and_action',
}


def _extract_select(view_sql: str) -> str:
    """Return the SELECT body from a CREATE OR REPLACE VIEW ... AS <select> file."""
    match = re.search(r'(?i)CREATE\s+OR\s+REPLACE\s+VIEW\s+\S+\s+AS\s*\n', view_sql)
    return view_sql[match.end():].strip() if match else view_sql.strip()


class PrestoSQLBatchEngine:
    """
    Executes the daily SQL batch pipeline (vw1–vw6) via Presto.

    run(view_name, execution_date):
      - vw1/vw2: executes CREATE OR REPLACE VIEW to keep the view definition current.
      - vw3–vw6: materialises the view output into a partitioned Hive PARQUET table.
                 Uses CTAS on first run (table absent) and INSERT OVERWRITE on subsequent
                 runs. Session is reset to APPEND in a finally block.

    read(view_name, execution_date):
      - Queries the materialised table for the given run_date partition and returns
        a pandas DataFrame.
    """

    def __init__(self, query_client, sql_dir=None):
        self._qc = query_client
        self._sql_dir = Path(sql_dir) if sql_dir else (
            Path(__file__).parent.parent.parent.parent / 'sql' / 'views'
        )

    def run(self, view_name, execution_date, max_retries=3):
        import time
        sql_file = self._sql_dir / f'{view_name}.sql'
        view_sql = sql_file.read_text()

        mat_table    = _MAT_SUFFIX[view_name]
        date_col     = _DATE_COL[view_name]
        fq_table     = f'hive.credit_engine.{mat_table}'
        select_sql   = _extract_select(view_sql)
        table_exists = self._table_exists(mat_table)

        for attempt in range(max_retries):
            try:
                if not table_exists:
                    _log.info('CTAS materialise %s for %s', view_name, execution_date)
                    self._qc.execute(
                        f"CREATE TABLE {fq_table} "
                        f"WITH (format = 'PARQUET', partitioned_by = ARRAY['run_date']) AS "
                        f"SELECT t.*, '{execution_date}' AS run_date "
                        f"FROM (\n{select_sql}\n) t "
                        f"WHERE t.{date_col} = DATE '{execution_date}'"
                    )
                else:
                    _log.info('INSERT OVERWRITE materialise %s for %s', view_name, execution_date)
                    self._qc.execute(
                        "SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE'"
                    )
                    try:
                        self._qc.execute(
                            f"INSERT INTO {fq_table} "
                            f"SELECT t.*, '{execution_date}' AS run_date "
                            f"FROM (\n{select_sql}\n) t "
                            f"WHERE t.{date_col} = DATE '{execution_date}'"
                        )
                    finally:
                        try:
                            self._qc.execute(
                                "SET SESSION hive.insert_existing_partitions_behavior = 'APPEND'"
                            )
                        except Exception as reset_exc:
                            msg = (
                                f'batch_engine: failed to reset '
                                f'insert_existing_partitions_behavior to APPEND '
                                f'after materialising {view_name}: {reset_exc}. '
                                f'Session may remain in OVERWRITE mode.'
                            )
                            _log.error(msg)
                            warnings.warn(msg, RuntimeWarning, stacklevel=2)
                return
            except Exception as exc:
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        f'batch_engine: materialise {view_name} failed after '
                        f'{max_retries} attempts for date={execution_date}: {exc}'
                    ) from exc
                _log.warning(
                    'Retrying materialise %s (attempt %d): %s', view_name, attempt + 1, exc
                )
                time.sleep(2 ** attempt)

    def read(self, view_name, execution_date):
        mat_table = _MAT_SUFFIX[view_name]
        _log.info('Reading %s for %s', mat_table, execution_date)
        return self._qc.query(
            f"SELECT * FROM hive.credit_engine.{mat_table} "
            f"WHERE run_date = '{execution_date}'"
        ).to_dataframe()

    def _table_exists(self, table_name):
        df = self._qc.query(
            f"SELECT COUNT(*) AS cnt FROM information_schema.tables "
            f"WHERE table_schema = 'credit_engine' AND table_name = '{table_name}'"
        ).to_dataframe()
        return int(df.iloc[0]['cnt']) > 0
