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


# ════════════ LEAST-PRIVILEGE DB USER GENERATION ════════════
# source role: STRATA hanya perlu baca (SELECT) + hapus setelah verify (DELETE) untuk purge.
# target role: STRATA hanya perlu baca (SELECT, untuk verify count) + tulis (INSERT) untuk copy.
# Tidak pernah UPDATE, DROP, ALTER, atau grant di luar schema yang diminta.
_PRIVS = {"source": ("SELECT", "DELETE"), "target": ("SELECT", "INSERT")}


def generate_user_sql(engine: str, role: str, schema: str, username: str, password: str) -> str:
    """Generate CREATE USER + least-privilege GRANT script. Tidak dieksekusi oleh STRATA —
    hanya ditampilkan untuk dijalankan manual oleh DBA setelah direview."""
    privs = _PRIVS[role]
    priv_list = ", ".join(privs)

    if engine == "mysql":
        return (
            f"-- Least privilege untuk role={role}, schema={schema}\n"
            f"CREATE USER '{username}'@'%' IDENTIFIED BY '{password}';\n"
            f"GRANT {priv_list} ON `{schema}`.* TO '{username}'@'%';\n"
            f"FLUSH PRIVILEGES;\n"
        )
    if engine == "postgresql":
        return (
            f"-- Least privilege untuk role={role}, schema={schema}\n"
            f"CREATE USER {username} WITH PASSWORD '{password}';\n"
            f"GRANT CONNECT ON DATABASE current_database() TO {username};\n"
            f"GRANT USAGE ON SCHEMA {schema} TO {username};\n"
            f"GRANT {priv_list} ON ALL TABLES IN SCHEMA {schema} TO {username};\n"
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT {priv_list} ON TABLES TO {username};\n"
        )
    # oracle — grant per-table via dynamic PL/SQL loop atas semua tabel di schema target,
    # karena Oracle tidak punya "GRANT ... ON ALL TABLES IN SCHEMA" seperti PG.
    return (
        f"-- Least privilege untuk role={role}, schema={schema}\n"
        f'CREATE USER {username} IDENTIFIED BY "{password}";\n'
        f"GRANT CREATE SESSION TO {username};\n"
        f"BEGIN\n"
        f"  FOR t IN (SELECT table_name FROM all_tables WHERE owner = '{schema.upper()}') LOOP\n"
        f"    EXECUTE IMMEDIATE 'GRANT {priv_list} ON \"{schema.upper()}\".\"' || t.table_name || '\" TO {username}';\n"
        f"  END LOOP;\n"
        f"END;\n"
        f"/\n"
    )
