-- ============================================================
-- STRATA Repository Schema (MySQL 8) — Fase 0
-- Jalankan sekali di MySQL yang lo siapkan:
--   mysql -u root -p < schema.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS strata CHARACTER SET utf8mb4;
USE strata;

-- 1. CONNECTIONS ------------------------------------------------
CREATE TABLE IF NOT EXISTS connections (
  id                 INT AUTO_INCREMENT PRIMARY KEY,
  name               VARCHAR(100) NOT NULL UNIQUE,
  role               ENUM('source','target') NOT NULL,
  engine             ENUM('oracle','postgresql','mysql') NOT NULL,
  host               VARCHAR(255) NOT NULL,
  port               INT NOT NULL,
  database_name      VARCHAR(128) NOT NULL,
  username           VARCHAR(128) NOT NULL,
  password_encrypted TEXT NOT NULL,
  status             ENUM('untested','ok','failed') DEFAULT 'untested',
  created_at         DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- 2. JOBS -------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
  id               INT AUTO_INCREMENT PRIMARY KEY,
  job_name         VARCHAR(200) NOT NULL UNIQUE,
  src_conn_id      INT NOT NULL,
  tgt_conn_id      INT NOT NULL,
  src_schema       VARCHAR(128) NOT NULL,
  tgt_schema       VARCHAR(128) NOT NULL,
  table_name       VARCHAR(128) NOT NULL,
  watermark_col    VARCHAR(128) NOT NULL,
  retention_days   INT NOT NULL DEFAULT 90,
  mode             ENUM('delete','keep') NOT NULL DEFAULT 'delete',
  batch_size       INT NOT NULL DEFAULT 5000,
  batch_delay_ms   INT NOT NULL DEFAULT 200,
  approval_status  ENUM('needs_approval','approved') DEFAULT 'needs_approval',
  active_version   INT DEFAULT 0,
  schedule_enabled TINYINT(1) DEFAULT 0,
  schedule_preset  VARCHAR(20) DEFAULT 'daily',
  schedule_time    VARCHAR(5)  DEFAULT '02:00',
  log_connection   VARCHAR(100) DEFAULT 'STRATA_REPO',
  created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (src_conn_id) REFERENCES connections(id),
  FOREIGN KEY (tgt_conn_id) REFERENCES connections(id)
) ENGINE=InnoDB;

-- 3. JOB_FILES (KJB/KTR versioned) ------------------------------
CREATE TABLE IF NOT EXISTS job_files (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  job_id       INT NOT NULL,
  version      INT NOT NULL,
  file_type    ENUM('kjb','ktr','ktr_counts','ktr_maxid') NOT NULL,
  file_content MEDIUMTEXT NOT NULL,
  dialect      VARCHAR(20) NOT NULL,
  approved     TINYINT(1) DEFAULT 0,
  approved_at  DATETIME NULL,
  is_active    TINYINT(1) DEFAULT 0,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
  UNIQUE KEY uq_job_ver_type (job_id, version, file_type)
) ENGINE=InnoDB;

-- 4. TASK_SUMMARY (ditulis Pentaho) ------------------------------
CREATE TABLE IF NOT EXISTS task_summary (
  run_id       VARCHAR(40) PRIMARY KEY,
  job_id       INT NOT NULL,
  job_name     VARCHAR(200),
  table_name   VARCHAR(128),
  cutoff_time  DATETIME,
  total_rows   BIGINT DEFAULT 0,
  batches      INT DEFAULT 0,
  mode         VARCHAR(10),
  final_status ENUM('RUNNING','OK','MISMATCH','SKIPPED','ERROR') DEFAULT 'RUNNING',
  skip_reason  VARCHAR(200) NULL,
  duration_sec DECIMAL(10,2) DEFAULT 0,
  started_at   DATETIME,
  finished_at  DATETIME NULL,
  INDEX idx_job_date (job_id, started_at),
  INDEX idx_status (final_status)
) ENGINE=InnoDB;

-- 5. TASK_DETAIL (per batch, ditulis Pentaho) --------------------
CREATE TABLE IF NOT EXISTS task_detail (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  run_id        VARCHAR(40) NOT NULL,
  batch_no      INT NOT NULL,
  phase         ENUM('cutoff','copy','verify','purge','keep') NOT NULL,
  rows_affected BIGINT DEFAULT 0,
  source_count  BIGINT NULL,
  target_count  BIGINT NULL,
  status        VARCHAR(20),
  error_msg     TEXT NULL,
  started_at    DATETIME,
  finished_at   DATETIME NULL,
  INDEX idx_run (run_id)
) ENGINE=InnoDB;

-- 6. ARCHIVED_KEYS (mode=keep) -----------------------------------
CREATE TABLE IF NOT EXISTS archived_keys (
  id           BIGINT AUTO_INCREMENT PRIMARY KEY,
  job_id       INT NOT NULL,
  run_id       VARCHAR(40) NOT NULL,
  range_cutoff DATETIME NOT NULL,
  created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_job (job_id)
) ENGINE=InnoDB;
