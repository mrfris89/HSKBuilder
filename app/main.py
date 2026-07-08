"""STRATA — Housekeeping Job Generator & Monitor. Port 5200."""
import io
import re
import zipfile
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .database import q, encrypt, decrypt
from . import generator, dialects

app = FastAPI(title="STRATA", version="1.0")


# ══════════════ MODELS ══════════════
class ConnIn(BaseModel):
    name: str
    role: str            # source | target
    engine: str          # oracle | postgresql | mysql
    host: str
    port: int
    database_name: str
    username: str
    password: str


class GenUserIn(BaseModel):
    conn_id: int
    schema_name: str
    table_name: str
    database_name: str | None = None  # override — Postgres bisa beda database dari default connection


class JobIn(BaseModel):
    job_name: str
    src_conn_id: int
    tgt_conn_id: int
    src_schema: str
    tgt_schema: str
    table_name: str
    watermark_col: str
    retention_days: int = 90
    mode: str = "delete"
    batch_size: int = 5000
    batch_delay_ms: int = 200
    schedule_enabled: bool = False
    schedule_time: str = "02:00"


# ══════════════ FASE 2: ADMIN / CONNECTIONS ══════════════
@app.get("/api/connections")
def list_conns(role: str | None = None):
    sql = "SELECT id,name,role,engine,host,port,database_name,username,status,created_at FROM connections"
    if role:
        return q(sql + " WHERE role=%s ORDER BY name", (role,))
    return q(sql + " ORDER BY name")


@app.post("/api/connections")
def create_conn(c: ConnIn):
    if c.role not in ("source", "target"):
        raise HTTPException(400, "role harus source/target")
    if c.engine not in ("oracle", "postgresql", "mysql"):
        raise HTTPException(400, "engine tidak valid")
    dup = q("SELECT id FROM connections WHERE name=%s", (c.name,))
    if dup:
        raise HTTPException(409, f"Nama connection '{c.name}' sudah dipakai")
    q("""INSERT INTO connections (name,role,engine,host,port,database_name,username,password_encrypted)
         VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
      (c.name, c.role, c.engine, c.host, c.port, c.database_name,
       c.username, encrypt(c.password)), fetch=False)
    return {"ok": True}


@app.put("/api/connections/{cid}")
def update_conn(cid: int, c: ConnIn):
    q("""UPDATE connections SET name=%s,role=%s,engine=%s,host=%s,port=%s,
         database_name=%s,username=%s,password_encrypted=%s,status='untested' WHERE id=%s""",
      (c.name, c.role, c.engine, c.host, c.port, c.database_name,
       c.username, encrypt(c.password), cid), fetch=False)
    return {"ok": True}


@app.delete("/api/connections/{cid}")
def delete_conn(cid: int):
    used = q("SELECT id FROM jobs WHERE src_conn_id=%s OR tgt_conn_id=%s", (cid, cid))
    if used:
        raise HTTPException(409, "Connection dipakai job — hapus job dulu")
    q("DELETE FROM connections WHERE id=%s", (cid,), fetch=False)
    return {"ok": True}


@app.post("/api/connections/{cid}/test")
def test_conn(cid: int):
    rows = q("SELECT * FROM connections WHERE id=%s", (cid,))
    if not rows:
        raise HTTPException(404, "not found")
    c = rows[0]
    pwd = decrypt(c["password_encrypted"])
    try:
        if c["engine"] == "mysql":
            import mysql.connector
            cn = mysql.connector.connect(host=c["host"], port=c["port"],
                                         database=c["database_name"],
                                         user=c["username"], password=pwd,
                                         connection_timeout=6)
            cn.close()
        elif c["engine"] == "postgresql":
            import psycopg2
            cn = psycopg2.connect(host=c["host"], port=c["port"],
                                  dbname=c["database_name"],
                                  user=c["username"], password=pwd,
                                  connect_timeout=6)
            cn.close()
        else:  # oracle (thin mode, tanpa client)
            import oracledb
            dsn = f"{c['host']}:{c['port']}/{c['database_name']}"
            cn = oracledb.connect(user=c["username"], password=pwd, dsn=dsn)
            cn.close()
        q("UPDATE connections SET status='ok' WHERE id=%s", (cid,), fetch=False)
        return {"ok": True, "message": "connected"}
    except Exception as e:
        q("UPDATE connections SET status='failed' WHERE id=%s", (cid,), fetch=False)
        return {"ok": False, "message": str(e)[:300]}


_IDENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


@app.post("/api/connections/generate-user-sql")
def generate_user_sql(g: GenUserIn):
    """Generate least-privilege GRANT script (SELECT+DELETE utk source, SELECT+INSERT
    utk target) untuk user yang SUDAH ADA di connection (dibuat manual di Admin).
    STRATA tidak membuat user baru & tidak eksekusi SQL ini — DBA jalankan manual setelah review."""
    rows = q("SELECT * FROM connections WHERE id=%s", (g.conn_id,))
    if not rows:
        raise HTTPException(404, "connection tidak ditemukan")
    c = rows[0]
    database = (g.database_name or c["database_name"]).strip()
    # Oracle tidak pakai database dalam GRANT (cuma komentar) — validasi longgar utk engine itu saja.
    if c["engine"] != "oracle" and not _IDENT_RE.match(database):
        raise HTTPException(400, "database_name tidak valid — huruf/angka/underscore saja")
    if not _IDENT_RE.match(g.schema_name):
        raise HTTPException(400, "schema_name tidak valid — huruf/angka/underscore saja")
    if not _IDENT_RE.match(g.table_name):
        raise HTTPException(400, "table_name tidak valid — huruf/angka/underscore saja")
    sql = dialects.generate_user_sql(c["engine"], c["role"], database,
                                      g.schema_name, g.table_name, c["username"])
    return {"username": c["username"], "sql": sql}


# ══════════════ FASE 3-5: JOBS + GENERATOR + APPROVAL ══════════════
@app.get("/api/jobs")
def list_jobs():
    return q("""SELECT j.*, s.name AS src_name, s.engine AS src_engine, s.database_name AS src_db,
                       t.name AS tgt_name, t.engine AS tgt_engine, t.database_name AS tgt_db
                FROM jobs j
                JOIN connections s ON s.id=j.src_conn_id
                JOIN connections t ON t.id=j.tgt_conn_id
                ORDER BY j.job_name""")


@app.get("/api/jobs/{jid}")
def get_job(jid: int):
    rows = q("""SELECT j.*, s.name AS src_name, s.engine AS src_engine,
                       t.name AS tgt_name, t.engine AS tgt_engine
                FROM jobs j JOIN connections s ON s.id=j.src_conn_id
                JOIN connections t ON t.id=j.tgt_conn_id WHERE j.id=%s""", (jid,))
    if not rows:
        raise HTTPException(404, "not found")
    job = rows[0]
    job["versions"] = q("""SELECT DISTINCT version, approved, approved_at, is_active
                           FROM job_files WHERE job_id=%s ORDER BY version DESC""", (jid,))
    return job


def _gen_and_store(jid: int) -> int:
    """Generate KJB+KTR versi baru (belum approved). Return version."""
    job = q("SELECT * FROM jobs WHERE id=%s", (jid,))[0]
    src = q("SELECT * FROM connections WHERE id=%s", (job["src_conn_id"],))[0]
    tgt = q("SELECT * FROM connections WHERE id=%s", (job["tgt_conn_id"],))[0]
    # repo conn info utk log — pakai env yang sama dg strata
    import os
    repo = {"name": job.get("log_connection") or "STRATA_REPO",
            "host": os.environ.get("REPO_HOST", "localhost"),
            "port": int(os.environ.get("REPO_PORT", 3306)),
            "database_name": os.environ.get("REPO_DB", "strata"),
            "username": os.environ.get("REPO_USER", "strata")}
    ktr = generator.generate_ktr(job, src, tgt)
    kjb = generator.generate_kjb(job, src, tgt, repo)
    counts_ktr = generator.generate_counts_ktr(job, src, tgt)
    files = [("kjb", kjb), ("ktr", ktr), ("ktr_counts", counts_ktr)]
    if job["watermark_col"].strip().upper() == "ID":
        files.append(("ktr_maxid", generator.generate_maxid_ktr(job, tgt)))
    ver = (q("SELECT COALESCE(MAX(version),0) AS v FROM job_files WHERE job_id=%s",
             (jid,))[0]["v"]) + 1
    dial = src["engine"]
    for ftype, content in files:
        q("""INSERT INTO job_files (job_id,version,file_type,file_content,dialect)
             VALUES (%s,%s,%s,%s,%s)""", (jid, ver, ftype, content, dial), fetch=False)
    return ver


@app.post("/api/jobs")
def create_job(j: JobIn):
    dup = q("SELECT id FROM jobs WHERE job_name=%s", (j.job_name,))
    if dup:
        raise HTTPException(409, {"error": "duplicate_name",
                                   "message": f"Job name '{j.job_name}' sudah dipakai",
                                   "existing_job_id": dup[0]["id"]})
    src = q("SELECT * FROM connections WHERE id=%s AND role='source'", (j.src_conn_id,))
    tgt = q("SELECT * FROM connections WHERE id=%s AND role='target'", (j.tgt_conn_id,))
    if not src:
        raise HTTPException(400, "src_conn_id bukan connection role=source")
    if not tgt:
        raise HTTPException(400, "tgt_conn_id bukan connection role=target")
    jid = q("""INSERT INTO jobs (job_name,src_conn_id,tgt_conn_id,src_schema,tgt_schema,
               table_name,watermark_col,retention_days,mode,batch_size,batch_delay_ms,
               schedule_enabled,schedule_time)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (j.job_name, j.src_conn_id, j.tgt_conn_id, j.src_schema, j.tgt_schema,
             j.table_name, j.watermark_col, j.retention_days, j.mode,
             j.batch_size, j.batch_delay_ms, int(j.schedule_enabled),
             j.schedule_time), fetch=False)
    ver = _gen_and_store(jid)
    return {"ok": True, "job_id": jid, "version": ver}


@app.put("/api/jobs/{jid}")
def update_job(jid: int, j: JobIn):
    """Edit job → re-generate → approval lama gugur."""
    dup = q("SELECT id FROM jobs WHERE job_name=%s AND id!=%s", (j.job_name, jid))
    if dup:
        raise HTTPException(409, {"error": "duplicate_name",
                                   "message": f"Job name '{j.job_name}' sudah dipakai",
                                   "existing_job_id": dup[0]["id"]})
    q("""UPDATE jobs SET job_name=%s,src_conn_id=%s,tgt_conn_id=%s,src_schema=%s,
         tgt_schema=%s,table_name=%s,watermark_col=%s,retention_days=%s,mode=%s,
         batch_size=%s,batch_delay_ms=%s,schedule_enabled=%s,schedule_time=%s,
         approval_status='needs_approval' WHERE id=%s""",
      (j.job_name, j.src_conn_id, j.tgt_conn_id, j.src_schema, j.tgt_schema,
       j.table_name, j.watermark_col, j.retention_days, j.mode, j.batch_size,
       j.batch_delay_ms, int(j.schedule_enabled), j.schedule_time, jid), fetch=False)
    q("UPDATE job_files SET is_active=0 WHERE job_id=%s", (jid,), fetch=False)
    ver = _gen_and_store(jid)
    return {"ok": True, "version": ver, "note": "versi lama gugur, wajib re-approve"}


@app.delete("/api/jobs/{jid}")
def delete_job(jid: int):
    q("DELETE FROM jobs WHERE id=%s", (jid,), fetch=False)
    return {"ok": True}


@app.get("/api/jobs/{jid}/files")
def job_files(jid: int, version: int | None = None):
    if version is None:
        v = q("SELECT COALESCE(MAX(version),0) AS v FROM job_files WHERE job_id=%s", (jid,))[0]["v"]
    else:
        v = version
    return q("""SELECT version,file_type,file_content,dialect,approved,approved_at,is_active
                FROM job_files WHERE job_id=%s AND version=%s""", (jid, v))


@app.post("/api/jobs/{jid}/approve")
def approve(jid: int, version: int):
    files = q("SELECT id FROM job_files WHERE job_id=%s AND version=%s", (jid, version))
    if not files:
        raise HTTPException(404, "versi tidak ditemukan")
    q("UPDATE job_files SET is_active=0 WHERE job_id=%s", (jid,), fetch=False)
    q("""UPDATE job_files SET approved=1, approved_at=NOW(), is_active=1
         WHERE job_id=%s AND version=%s""", (jid, version), fetch=False)
    q("UPDATE jobs SET approval_status='approved', active_version=%s WHERE id=%s",
      (version, jid), fetch=False)
    return {"ok": True, "approved_version": version}


_FILE_TYPE_SUFFIX = {"kjb": ".kjb", "ktr": ".ktr", "ktr_counts": "_counts.ktr", "ktr_maxid": "_maxid.ktr"}


def _filename_for(job_name: str, file_type: str) -> str:
    """Nama file fisik — HARUS sama persis dengan yang direferensikan job entry TRANS
    di dalam KJB (generator.py), karena Kitchen mencari file ini relatif ke folder KJB."""
    return f"{job_name}{_FILE_TYPE_SUFFIX.get(file_type, '.' + file_type)}"


@app.get("/api/jobs/{jid}/download")
def download(jid: int):
    """ZIP berisi KJB + semua KTR (main, counts, maxid) versi active approved."""
    job = q("SELECT * FROM jobs WHERE id=%s", (jid,))
    if not job:
        raise HTTPException(404)
    job = job[0]
    if job["approval_status"] != "approved":
        raise HTTPException(403, "Job belum approved — download dikunci")
    files = q("""SELECT file_type,file_content FROM job_files
                 WHERE job_id=%s AND is_active=1""", (jid,))
    if not files:
        raise HTTPException(404, "file active tidak ditemukan")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(_filename_for(job["job_name"], f["file_type"]), f["file_content"])
    buf.seek(0)
    return Response(buf.read(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{job["job_name"]}.zip"'})


# ══════════════ FASE 6: MONITOR HISTORICAL ══════════════
@app.get("/api/monitor/summary")
def monitor(date_from: str, date_to: str, rdbms: str | None = None,
            job_like: str | None = None, status: str | None = None):
    """DateFrom & DateTo mandatory (parameter wajib FastAPI)."""
    sql = """SELECT ts.*, j.src_conn_id, c.engine AS src_engine
             FROM task_summary ts
             LEFT JOIN jobs j ON j.id=ts.job_id
             LEFT JOIN connections c ON c.id=j.src_conn_id
             WHERE ts.started_at >= %s AND ts.started_at < DATE_ADD(%s, INTERVAL 1 DAY)"""
    params = [date_from, date_to]
    if rdbms:
        sql += " AND c.engine=%s"; params.append(rdbms)
    if job_like:
        sql += " AND ts.job_name LIKE %s"; params.append(f"%{job_like}%")
    if status:
        sql += " AND ts.final_status=%s"; params.append(status)
    sql += " ORDER BY ts.started_at DESC LIMIT 500"
    return q(sql, params)


@app.get("/api/monitor/detail/{run_id}")
def monitor_detail(run_id: str):
    return q("SELECT * FROM task_detail WHERE run_id=%s ORDER BY batch_no, id", (run_id,))


@app.get("/api/defaults/dates")
def default_dates():
    return {"date_from": str(date.today() - timedelta(days=3)),
            "date_to": str(date.today())}


# ══════════════ FRONTEND ══════════════
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return open("app/static/index.html").read()
