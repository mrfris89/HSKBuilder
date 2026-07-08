"""SQL dialect adapter — isolasi sintaks per engine.

Analogi: colokan listrik beda negara → adapter per engine.
"""

ABBR = {"oracle": "ora", "postgresql": "pgs", "mysql": "msq"}


def cutoff_expr(engine: str, days: int) -> str:
    """Ekspresi cutoff = 00:00 hari-H minus retensi."""
    if engine == "oracle":
        return f"TRUNC(SYSDATE) - {days}"
    if engine == "postgresql":
        return f"date_trunc('day', now()) - INTERVAL '{days} days'"
    return f"TIMESTAMP(CURDATE()) - INTERVAL {days} DAY"  # mysql


def select_batch_sql(engine, schema, table, wm, days, batch):
    cut = cutoff_expr(engine, days)
    base = f"SELECT * FROM {schema}.{table} WHERE {wm} < {cut} ORDER BY {wm}"
    if engine == "oracle":
        return f"SELECT * FROM ({base}) WHERE ROWNUM <= {batch}"
    return f"{base} LIMIT {batch}"


def count_sql(engine, schema, table, wm, days):
    cut = cutoff_expr(engine, days)
    return f"SELECT COUNT(*) AS cnt FROM {schema}.{table} WHERE {wm} < {cut}"


def delete_batch_sql(engine, schema, table, wm, days, batch):
    cut = cutoff_expr(engine, days)
    if engine == "oracle":
        return (f"DELETE FROM {schema}.{table} "
                f"WHERE {wm} < {cut} AND ROWNUM <= {batch}")
    if engine == "postgresql":
        return (f"DELETE FROM {schema}.{table} WHERE ctid IN ("
                f"SELECT ctid FROM {schema}.{table} "
                f"WHERE {wm} < {cut} LIMIT {batch})")
    return (f"DELETE FROM {schema}.{table} "
            f"WHERE {wm} < {cut} LIMIT {batch}")  # mysql


# ════════════ ID-BASED WATERMARKING ════════════

def get_max_id_sql(engine, schema, table, id_col):
    """Query to get the maximum ID value from target table."""
    return f"SELECT COALESCE(MAX({id_col}), 0) AS max_id FROM {schema}.{table}"


def select_batch_sql_by_id(engine, schema, table, id_col, batch):
    """Select rows with ID > max_id, ordered by ID, limited by batch size."""
    base = f"SELECT * FROM {schema}.{table} WHERE {id_col} > ${{MAX_ID}} ORDER BY {id_col}"
    if engine == "oracle":
        return f"SELECT * FROM ({base}) WHERE ROWNUM <= {batch}"
    return f"{base} LIMIT {batch}"


def count_sql_by_id(engine, schema, table, id_col):
    """Count rows with ID > max_id."""
    return f"SELECT COUNT(*) AS cnt FROM {schema}.{table} WHERE {id_col} > ${{MAX_ID}}"


def delete_batch_sql_by_id(engine, schema, table, id_col, batch):
    """Delete rows with ID > max_id, limited by batch size."""
    if engine == "oracle":
        return (f"DELETE FROM {schema}.{table} "
                f"WHERE {id_col} > ${{MAX_ID}} AND ROWNUM <= {batch}")
    if engine == "postgresql":
        return (f"DELETE FROM {schema}.{table} WHERE ctid IN ("
                f"SELECT ctid FROM {schema}.{table} "
                f"WHERE {id_col} > ${{MAX_ID}} LIMIT {batch})")
    return (f"DELETE FROM {schema}.{table} "
            f"WHERE {id_col} > ${{MAX_ID}} LIMIT {batch}")  # mysql


def jdbc_type(engine: str) -> str:
    return {"oracle": "ORACLE", "postgresql": "POSTGRESQL", "mysql": "MYSQL"}[engine]
