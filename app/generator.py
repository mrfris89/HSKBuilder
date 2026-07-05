"""Generator file .kjb & .ktr — XML valid format Pentaho Kettle (Community Edition).

Struktur pipeline: CUTOFF -> COPY -> VERIFY (hard gate) -> PURGE/KEEP.
VERIFY gate dikodekan sebagai evaluasi kondisi di KJB:
PURGE hanya jalan bila hop 'sukses' dari VERIFY.
"""
from xml.sax.saxutils import escape
from . import dialects as D


def _conn_block(name, engine, host, port, dbname, user):
    """Named connection block (password TIDAK ditulis — user set di Spoon)."""
    ctype = {"oracle": "ORACLE", "postgresql": "POSTGRESQL", "mysql": "MYSQL"}[engine]
    access = "Native"
    return f"""  <connection>
    <name>{escape(name)}</name>
    <server>{escape(host)}</server>
    <type>{ctype}</type>
    <access>{access}</access>
    <database>{escape(dbname)}</database>
    <port>{port}</port>
    <username>{escape(user)}</username>
    <password>Encrypted </password>
    <attributes/>
  </connection>"""


def generate_ktr(job: dict, src: dict, tgt: dict) -> str:
    """KTR = transformation: READ_SOURCE -> WRITE_TARGET."""
    eng_s = src["engine"]
    sel = D.select_batch_sql(eng_s, job["src_schema"], job["table_name"],
                             job["watermark_col"], job["retention_days"],
                             job["batch_size"])
    name = job["job_name"]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<transformation>
  <info>
    <name>{escape(name)}</name>
    <description>STRATA housekeeping COPY: {escape(job['src_schema'])}.{escape(job['table_name'])} -&gt; {escape(job['tgt_schema'])}.{escape(job['table_name'])} | retensi {job['retention_days']}d | batch {job['batch_size']}</description>
    <trans_version/>
    <trans_type>Normal</trans_type>
  </info>
{_conn_block(src['name'], src['engine'], src['host'], src['port'], src['database_name'], src['username'])}
{_conn_block(tgt['name'], tgt['engine'], tgt['host'], tgt['port'], tgt['database_name'], tgt['username'])}
  <order>
    <hop><from>READ_SOURCE</from><to>WRITE_TARGET</to><enabled>Y</enabled></hop>
  </order>
  <step>
    <name>READ_SOURCE</name>
    <type>TableInput</type>
    <connection>{escape(src['name'])}</connection>
    <sql>{escape(sel)}</sql>
    <limit>0</limit>
    <execute_each_row>N</execute_each_row>
    <GUI>
      <xloc>150</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </GUI>
  </step>
  <step>
    <name>WRITE_TARGET</name>
    <type>TableOutput</type>
    <connection>{escape(tgt['name'])}</connection>
    <schema>{escape(job['tgt_schema'])}</schema>
    <table>{escape(job['table_name'])}</table>
    <commit>{job['batch_size']}</commit>
    <truncate>N</truncate>
    <ignore_errors>N</ignore_errors>
    <use_batch>Y</use_batch>
    <GUI>
      <xloc>450</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </GUI>
  </step>
</transformation>
"""


def generate_kjb(job: dict, src: dict, tgt: dict, repo: dict) -> str:
    """KJB = orchestrator 4 fase dengan VERIFY hard gate.

    Alur hop:
      START -> INIT_SUMMARY -> RUN_KTR -> VERIFY_COUNT
      VERIFY sukses -> PURGE/KEEP -> FINALIZE_OK
      VERIFY gagal  -> MARK_MISMATCH (ABORT)
    """
    eng_s = src["engine"]
    j = job
    cnt_src = D.count_sql(eng_s, j["src_schema"], j["table_name"],
                          j["watermark_col"], j["retention_days"])
    cnt_tgt = D.count_sql(tgt["engine"], j["tgt_schema"], j["table_name"],
                          j["watermark_col"], j["retention_days"])
    cutoff_mysql = D.cutoff_expr("mysql", j["retention_days"])

    if j["mode"] == "delete":
        purge_sql = D.delete_batch_sql(eng_s, j["src_schema"], j["table_name"],
                                       j["watermark_col"], j["retention_days"],
                                       j["batch_size"])
        purge_entry = f"""    <entry>
      <name>PURGE_SOURCE</name>
      <type>SQL</type>
      <connection>{escape(src['name'])}</connection>
      <sql>{escape(purge_sql)}</sql>
      <xloc>1050</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>"""
        last_phase = "PURGE_SOURCE"
    else:
        keep_sql = (f"INSERT INTO archived_keys (job_id, run_id, range_cutoff) "
                    f"VALUES ({j['id']}, '${{RUN_ID}}', {cutoff_mysql})")
        purge_entry = f"""    <entry>
      <name>KEEP_LOG</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(keep_sql)}</sql>
      <xloc>1050</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>"""
        last_phase = "KEEP_LOG"

    init_sql = (
        f"SET @run_id = CONCAT('HK-', UNIX_TIMESTAMP()); "
        f"INSERT INTO task_summary (run_id, job_id, job_name, table_name, cutoff_time, mode, final_status, started_at) "
        f"VALUES (@run_id, {j['id']}, '{j['job_name']}', '{j['table_name']}', {cutoff_mysql}, '{j['mode']}', 'RUNNING', NOW());"
    )
    fin_ok = (f"UPDATE task_summary SET final_status='OK', finished_at=NOW(), "
              f"duration_sec=TIMESTAMPDIFF(SECOND, started_at, NOW()) "
              f"WHERE job_id={j['id']} AND final_status='RUNNING';")
    fin_bad = (f"UPDATE task_summary SET final_status='MISMATCH', finished_at=NOW() "
               f"WHERE job_id={j['id']} AND final_status='RUNNING';")

    verify_js = f"""var src_c = -1; var tgt_c = -2;
// STRATA VERIFY hard gate — count source vs target
// Query dieksekusi via child entries; nilai diisi Pentaho variable.
// Jika mismatch, entry ini return false -> hop gagal -> MARK_MISMATCH.
src_c = parseInt(parent_job.getVariable("SRC_COUNT","-1"));
tgt_c = parseInt(parent_job.getVariable("TGT_COUNT","-2"));
result = (src_c == tgt_c);"""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<job>
  <name>{escape(j['job_name'])}</name>
  <description>STRATA housekeeping: CUTOFF -&gt; COPY -&gt; VERIFY -&gt; {'PURGE' if j['mode']=='delete' else 'KEEP'} | {escape(j['src_schema'])}.{escape(j['table_name'])} retensi {j['retention_days']}d</description>
{_conn_block(repo['name'], 'mysql', repo['host'], repo['port'], repo['database_name'], repo['username'])}
{_conn_block(src['name'], src['engine'], src['host'], src['port'], src['database_name'], src['username'])}
{_conn_block(tgt['name'], tgt['engine'], tgt['host'], tgt['port'], tgt['database_name'], tgt['username'])}
  <entries>
    <entry>
      <name>START</name>
      <type>SPECIAL</type>
      <start>Y</start>
      <xloc>50</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>INIT_SUMMARY</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(init_sql)}</sql>
      <xloc>200</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>RUN_KTR</name>
      <type>TRANS</type>
      <filename>${{Internal.Entry.Current.Directory}}/{escape(j['job_name'])}.ktr</filename>
      <xloc>370</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>GET_SRC_COUNT</name>
      <type>EVAL_TABLE_CONTENT</type>
      <connection>{escape(src['name'])}</connection>
      <sql>{escape(cnt_src)}</sql>
      <success_condition>rows_count_greater_equal</success_condition>
      <limit>0</limit>
      <xloc>540</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>GET_TGT_COUNT</name>
      <type>EVAL_TABLE_CONTENT</type>
      <connection>{escape(tgt['name'])}</connection>
      <sql>{escape(cnt_tgt)}</sql>
      <success_condition>rows_count_greater_equal</success_condition>
      <limit>0</limit>
      <xloc>710</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>VERIFY_COUNT</name>
      <type>EVAL</type>
      <script>{escape(verify_js)}</script>
      <xloc>880</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
{purge_entry}
    <entry>
      <name>FINALIZE_OK</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(fin_ok)}</sql>
      <xloc>1220</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>MARK_MISMATCH</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(fin_bad)}</sql>
      <xloc>880</xloc>
      <yloc>370</yloc>
      <draw>Y</draw>
    </entry>
    <entry>
      <name>ABORT_JOB</name>
      <type>ABORT</type>
      <message>STRATA VERIFY MISMATCH - purge dibatalkan</message>
      <xloc>1050</xloc>
      <yloc>370</yloc>
      <draw>Y</draw>
    </entry>
  </entries>
  <hops>
    <hop><from>START</from><to>INIT_SUMMARY</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>Y</unconditional></hop>
    <hop><from>INIT_SUMMARY</from><to>RUN_KTR</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
    <hop><from>RUN_KTR</from><to>GET_SRC_COUNT</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
    <hop><from>GET_SRC_COUNT</from><to>GET_TGT_COUNT</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>Y</unconditional></hop>
    <hop><from>GET_TGT_COUNT</from><to>VERIFY_COUNT</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>Y</unconditional></hop>
    <!-- HARD GATE: sukses -> purge/keep, gagal -> mismatch+abort -->
    <hop><from>VERIFY_COUNT</from><to>{last_phase}</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
    <hop><from>VERIFY_COUNT</from><to>MARK_MISMATCH</to><enabled>Y</enabled><evaluation>N</evaluation><unconditional>N</unconditional></hop>
    <hop><from>{last_phase}</from><to>FINALIZE_OK</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
    <hop><from>MARK_MISMATCH</from><to>ABORT_JOB</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>Y</unconditional></hop>
  </hops>
</job>
"""
