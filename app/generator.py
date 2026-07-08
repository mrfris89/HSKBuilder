"""Generator file .kjb & .ktr — XML valid format Pentaho Kettle (Community Edition).

Struktur pipeline: CUTOFF -> COPY -> VERIFY (hard gate) -> PURGE/KEEP.
VERIFY gate dikodekan sebagai evaluasi kondisi di KJB:
PURGE hanya jalan bila hop 'sukses' dari VERIFY.

ID-based watermarking: Jika watermark_col == 'ID', cek max ID di target dulu,
lalu transfer data dari max_id+1 ke terakhir.
"""
from xml.sax.saxutils import escape
from . import dialects as D


def _is_id_watermark(watermark_col: str) -> bool:
    """Check if watermark column is ID (case-insensitive)."""
    return watermark_col.upper() == "ID"


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
    
    if _is_id_watermark(job["watermark_col"]):
        sel = D.select_batch_sql_by_id(eng_s, job["src_schema"], job["table_name"],
                                       job["watermark_col"], job["batch_size"])
        wm_desc = f"ID watermark"
    else:
        sel = D.select_batch_sql(eng_s, job["src_schema"], job["table_name"],
                                 job["watermark_col"], job["retention_days"],
                                 job["batch_size"])
        wm_desc = f"retensi {job['retention_days']}d"
    
    name = job["job_name"]
    
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<transformation>
  <info>
    <name>{escape(name)}</name>
    <description>STRATA housekeeping COPY: {escape(job['src_schema'])}.{escape(job['table_name'])} -&gt; {escape(job['tgt_schema'])}.{escape(job['table_name'])} | {wm_desc} | batch {job['batch_size']}</description>
    <trans_version/>
    <trans_type>Normal</trans_type>
  </info>
{_conn_block(src['name'], src['engine'], src['host'], src['port'], src['database_name'], src['username'])}
{_conn_block(tgt['name'], tgt['engine'], tgt['host'], tgt['port'], tgt['database_name'], tgt['username'])}
  <notepads>
  </notepads>
  <order>
    <hop><from>READ_SOURCE</from><to>WRITE_TARGET</to><enabled>Y</enabled></hop>
  </order>
  <step>
    <name>READ_SOURCE</name>
    <type>TableInput</type>
    <description/>
    <distribute>Y</distribute>
    <custom_distribution/>
    <copies>1</copies>
    <partitioning>
      <method>none</method>
      <schema_name/>
    </partitioning>
    <connection>{escape(src['name'])}</connection>
    <sql>{escape(sel)}</sql>
    <limit>0</limit>
    <lookup/>
    <execute_each_row>N</execute_each_row>
    <variables_active>N</variables_active>
    <lazy_conversion_active>N</lazy_conversion_active>
    <cluster_schema/>
    <remotesteps>
      <input>
      </input>
      <output>
      </output>
    </remotesteps>
    <GUI>
      <xloc>150</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </GUI>
  </step>
  <step>
    <name>WRITE_TARGET</name>
    <type>TableOutput</type>
    <description/>
    <distribute>Y</distribute>
    <custom_distribution/>
    <copies>1</copies>
    <partitioning>
      <method>none</method>
      <schema_name/>
    </partitioning>
    <connection>{escape(tgt['name'])}</connection>
    <schema>{escape(job['tgt_schema'])}</schema>
    <table>{escape(job['table_name'])}</table>
    <commit>{job['batch_size']}</commit>
    <truncate>N</truncate>
    <ignore_errors>N</ignore_errors>
    <use_batch>Y</use_batch>
    <specify_fields>N</specify_fields>
    <cluster_schema/>
    <remotesteps>
      <input>
      </input>
      <output>
      </output>
    </remotesteps>
    <GUI>
      <xloc>450</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
    </GUI>
  </step>
  <step_error_handling>
  </step_error_handling>
  <slave-step-copy-partition-distribution>
  </slave-step-copy-partition-distribution>
  <slave-transformation>N</slave-transformation>
</transformation>
"""


def generate_kjb(job: dict, src: dict, tgt: dict, repo: dict) -> str:
    """KJB = orchestrator 4-5 fase dengan VERIFY hard gate.

    Date-based flow:
      START -> INIT_SUMMARY -> RUN_KTR -> GET_SRC_COUNT -> GET_TGT_COUNT -> VERIFY_COUNT
      VERIFY sukses -> PURGE/KEEP -> FINALIZE_OK
      VERIFY gagal  -> MARK_MISMATCH (ABORT)
    
    ID-based flow:
      START -> INIT_SUMMARY -> GET_MAX_ID -> RUN_KTR -> GET_SRC_COUNT -> GET_TGT_COUNT -> VERIFY_COUNT
      VERIFY sukses -> PURGE/KEEP -> FINALIZE_OK
      VERIFY gagal  -> MARK_MISMATCH (ABORT)
    """
    eng_s = src["engine"]
    j = job
    is_id_wm = _is_id_watermark(j["watermark_col"])
    
    # Determine SQL queries based on watermark type
    if is_id_wm:
        cnt_src = D.count_sql_by_id(eng_s, j["src_schema"], j["table_name"],
                                    j["watermark_col"])
        cnt_tgt = D.count_sql_by_id(tgt["engine"], j["tgt_schema"], j["table_name"],
                                    j["watermark_col"])
        get_max_id_sql = D.get_max_id_sql(tgt["engine"], j["tgt_schema"], j["table_name"],
                                          j["watermark_col"])
        init_cutoff = "NOW()"  # Use timestamp for logging instead of cutoff
    else:
        cnt_src = D.count_sql(eng_s, j["src_schema"], j["table_name"],
                              j["watermark_col"], j["retention_days"])
        cnt_tgt = D.count_sql(tgt["engine"], j["tgt_schema"], j["table_name"],
                              j["watermark_col"], j["retention_days"])
        init_cutoff = D.cutoff_expr("mysql", j["retention_days"])

    if j["mode"] == "delete":
        if is_id_wm:
            purge_sql = D.delete_batch_sql_by_id(eng_s, j["src_schema"], j["table_name"],
                                                 j["watermark_col"], j["batch_size"])
        else:
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
      <parallel>N</parallel>
    </entry>"""
        last_phase = "PURGE_SOURCE"
    else:
        keep_sql = (f"INSERT INTO archived_keys (job_id, run_id, range_cutoff) "
                    f"VALUES ({j['id']}, '${{RUN_ID}}', {init_cutoff})")
        purge_entry = f"""    <entry>
      <name>KEEP_LOG</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(keep_sql)}</sql>
      <xloc>1050</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>"""
        last_phase = "KEEP_LOG"

    init_sql = (
        f"SET @run_id = CONCAT('HK-', UNIX_TIMESTAMP()); "
        f"INSERT INTO task_summary (run_id, job_id, job_name, table_name, cutoff_time, mode, final_status, started_at) "
        f"VALUES (@run_id, {j['id']}, '{j['job_name']}', '{j['table_name']}', {init_cutoff}, '{j['mode']}', 'RUNNING', NOW());"
    )
    
    # For ID watermark, add GET_MAX_ID entry
    get_max_id_entry = ""
    init_summary_to_next = "RUN_KTR"
    
    if is_id_wm:
        get_max_id_entry = f"""    <entry>
      <name>GET_MAX_ID</name>
      <type>EVAL_TABLE_CONTENT</type>
      <connection>{escape(tgt['name'])}</connection>
      <sql>{escape(get_max_id_sql)}</sql>
      <success_condition>rows_count_greater_equal</success_condition>
      <limit>0</limit>
      <xloc>280</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
"""
        init_summary_to_next = "GET_MAX_ID"
        max_id_hop = ('    <hop><from>GET_MAX_ID</from><to>RUN_KTR</to>'
                      '<enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>\n')
    else:
        max_id_hop = ""
    
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

    wm_type = "ID" if is_id_wm else f"retensi {j['retention_days']}d"

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<job>
  <name>{escape(j['job_name'])}</name>
  <description>STRATA housekeeping: CUTOFF -&gt; COPY -&gt; VERIFY -&gt; {'PURGE' if j['mode']=='delete' else 'KEEP'} | {escape(j['src_schema'])}.{escape(j['table_name'])} retensi {j['retention_days']}d</description>
{_conn_block(repo['name'], 'mysql', repo['host'], repo['port'], repo['database_name'], repo['username'])}
{_conn_block(src['name'], src['engine'], src['host'], src['port'], src['database_name'], src['username'])}
{_conn_block(tgt['name'], tgt['engine'], tgt['host'], tgt['port'], tgt['database_name'], tgt['username'])}
  <notepads>
  </notepads>
  <parameters>
  </parameters>
  <entries>
    <entry>
      <name>START</name>
      <type>SPECIAL</type>
      <start>Y</start>
      <xloc>50</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
    <entry>
      <name>INIT_SUMMARY</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(init_sql)}</sql>
      <xloc>200</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
    <entry>
      <name>RUN_KTR</name>
      <type>TRANS</type>
      <filename>${{Internal.Entry.Current.Directory}}/{escape(j['job_name'])}.ktr</filename>
      <xloc>370</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
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
      <parallel>N</parallel>
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
      <parallel>N</parallel>
    </entry>
    <entry>
      <name>VERIFY_COUNT</name>
      <type>EVAL</type>
      <script>{escape(verify_js)}</script>
      <xloc>880</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
{get_max_id_entry}{purge_entry}
    <entry>
      <name>FINALIZE_OK</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(fin_ok)}</sql>
      <xloc>1220</xloc>
      <yloc>200</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
    <entry>
      <name>MARK_MISMATCH</name>
      <type>SQL</type>
      <connection>{escape(repo['name'])}</connection>
      <sql>{escape(fin_bad)}</sql>
      <xloc>880</xloc>
      <yloc>370</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
    <entry>
      <name>ABORT_JOB</name>
      <type>ABORT</type>
      <message>STRATA VERIFY MISMATCH - purge dibatalkan</message>
      <xloc>1050</xloc>
      <yloc>370</yloc>
      <draw>Y</draw>
      <parallel>N</parallel>
    </entry>
  </entries>
  <hops>
    <hop><from>START</from><to>INIT_SUMMARY</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>Y</unconditional></hop>
    <hop><from>INIT_SUMMARY</from><to>{init_summary_to_next}</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
{max_id_hop}    <hop><from>RUN_KTR</from><to>GET_SRC_COUNT</to><enabled>Y</enabled><evaluation>Y</evaluation><unconditional>N</unconditional></hop>
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
