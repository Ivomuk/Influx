import pandas as pd
import prestodb


def make_query_client(host, port, user, password,
                      catalog='hive', schema='credit_engine',
                      http_scheme='https'):
    """
    Returns a PrestoQueryClient connected to the given PrestoDB coordinator.

    host        : Presto coordinator hostname or IP
    port        : Presto coordinator port (typically 8080 or 8443)
    user        : service-account username
    password    : service-account password
    catalog     : Presto catalog (default 'hive')
    schema      : default schema / Hive database (default 'credit_engine')
    http_scheme : 'https' for TLS (production), 'http' for local/dev
    """
    conn = prestodb.dbapi.connect(
        host=host,
        port=int(port),
        user=user,
        http_scheme=http_scheme,
        auth=prestodb.auth.BasicAuthentication(user, password),
        catalog=catalog,
        schema=schema,
    )
    return PrestoQueryClient(conn)


class PrestoQueryClient:
    """
    Thin adapter over presto-python-client exposing three methods:

      .query(sql).to_dataframe()     — used by run_certification SQL QA checks
      .execute(sql)                  — used for DDL / fire-and-forget DML
      .insert_rows(table, cols, rows) — chunked INSERT INTO … VALUES writes
    """

    def __init__(self, conn):
        self._conn = conn

    def query(self, sql_text):
        return _QueryResult(self._conn, sql_text)

    def execute(self, sql_text):
        cursor = self._conn.cursor()
        cursor.execute(sql_text)
        return cursor

    def insert_rows(self, table, columns, rows, chunk_size=500):
        """
        Inserts rows into a Hive table via chunked INSERT INTO … VALUES.

        table      : fully-qualified table name, e.g. 'credit_engine.pipeline_capacity_state'
        columns    : list of column names in insertion order
        rows       : iterable of tuples matching column order
        chunk_size : rows per INSERT statement (default 500)

        Values are escaped but NOT parameterised — suitable for trusted
        internal data only (no user-supplied strings reach this path).
        """
        col_list = ', '.join(columns)
        rows = list(rows)
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            value_clauses = ', '.join(
                '(' + ', '.join(_sql_literal(v) for v in row) + ')'
                for row in chunk
            )
            sql = f'INSERT INTO {table} ({col_list}) VALUES {value_clauses}'
            cursor = self._conn.cursor()
            cursor.execute(sql)


def _sql_literal(v):
    """Format a Python value as a Presto SQL literal."""
    if v is None:
        return 'NULL'
    if isinstance(v, bool):
        return 'TRUE' if v else 'FALSE'
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, pd.Timestamp):
        return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S')}'"
    escaped = str(v).replace("'", "''")
    return f"'{escaped}'"


class _QueryResult:
    def __init__(self, conn, sql):
        self._conn = conn
        self._sql = sql

    def to_dataframe(self):
        cursor = self._conn.cursor()
        cursor.execute(self._sql)
        rows = cursor.fetchall()
        if cursor.description is None:
            return pd.DataFrame()
        cols = [d[0] for d in cursor.description]
        return pd.DataFrame(rows, columns=cols) if rows else pd.DataFrame(columns=cols)
