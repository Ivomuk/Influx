import logging
import warnings
from pathlib import Path

_log = logging.getLogger(__name__)

# Views requiring materialisation: produce DataFrames consumed by the decision engine.
# vw1 and vw2 stay as lazy Presto views — Presto evaluates them inline as part of
# the vw3/vw4 materialisation query plan.
_MATERIALISE = {
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

_MAT_SUFFIX = {
    'vw3_credit_v1_layer1_features': 'mat_vw3_layer1_features',
    'vw4_credit_v1_layer0_scores':   'mat_vw4_layer0_scores',
    'vw5_credit_v1_reason_codes':    'mat_vw5_reason_codes',
    'vw6_credit_v1_cap_and_action':  'mat_vw6_cap_and_action',
}


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

        if view_name not in _MATERIALISE:
            _log.info('Refreshing Presto view: %s', view_name)
            self._qc.execute(view_sql)
            return

        mat_table   = _MAT_SUFFIX[view_name]
        date_col    = _DATE_COL[view_name]
        fq_mat      = f'hive.credit_engine.{mat_table}'
        fq_view     = f'hive.credit_engine.{view_name}'
        table_exists = self._table_exists(mat_table)

        for attempt in range(max_retries):
            try:
                if not table_exists:
                    _log.info('CTAS materialise %s for %s', view_name, execution_date)
                    self._qc.execute(
                        f"CREATE TABLE {fq_mat} "
                        f"WITH (format = 'PARQUET', partitioned_by = ARRAY['run_date']) AS "
                        f"SELECT t.*, '{execution_date}' AS run_date "
                        f"FROM {fq_view} t "
                        f"WHERE t.{date_col} = DATE '{execution_date}'"
                    )
                else:
                    _log.info('INSERT OVERWRITE materialise %s for %s', view_name, execution_date)
                    self._qc.execute(
                        "SET SESSION hive.insert_existing_partitions_behavior = 'OVERWRITE'"
                    )
                    try:
                        self._qc.execute(
                            f"INSERT INTO {fq_mat} "
                            f"SELECT t.*, '{execution_date}' AS run_date "
                            f"FROM {fq_view} t "
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
