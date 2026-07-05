# STRATA — Housekeeping Job Generator & Monitor

Web tool untuk generate file Pentaho `.kjb` + `.ktr` housekeeping data + monitoring log historis.
**STRATA tidak mengeksekusi data movement** — Pentaho yang eksekusi, STRATA generate & pantau.

## Arsitektur

```
STRATA UI (port 5200)  →  generate .kjb/.ktr  →  lo import ke Pentaho
       ↓ baca log                                     ↓ tulis log
MySQL Repository (strata)  ←──────────────────────────┘
```

## Cara Jalanin

### 1. Siapkan MySQL Repository (di luar container)
```bash
mysql -u root -p < schema.sql
# lalu buat user (sesuaikan):
mysql -u root -p -e "CREATE USER 'strata'@'%' IDENTIFIED BY 'strata123'; GRANT ALL ON strata.* TO 'strata'@'%'; FLUSH PRIVILEGES;"
```

### 2. Set environment (edit docker-compose.yml atau buat .env)
```
REPO_HOST=host.docker.internal   # atau IP MySQL lo
REPO_PORT=3306
REPO_DB=strata
REPO_USER=strata
REPO_PASSWORD=strata123
```

### 3. Build & run
```bash
docker compose up --build -d
```

Buka: **http://localhost:5200**

## Alur Pakai

1. **Admin** → tambah connection (role source & target) → Test
2. **Configuration → + New Job** → wizard 5 step → Save & Approve
3. **↓ KJB/KTR** → download zip
4. Import ke **Pentaho Spoon** (File → Open .kjb)
5. Di Spoon, verifikasi named connections (nama sama dengan di STRATA; **isi password manual di Spoon** — STRATA tidak menyematkan password di file)
6. Run manual di Spoon, atau via cron:
   ```
   0 2 * * * /opt/pentaho/data-integration/kitchen.sh -file=/opt/strata/jobs/ora_trxprod_transaction_90d.kjb -level=Basic >> /var/log/strata/job.log 2>&1
   ```
7. **Monitor** di STRATA → log run yang ditulis Pentaho ke MySQL

## Catatan Penting

- **Password tidak disematkan di KJB/KTR** (keamanan). Set di Spoon per named connection.
- File KJB & KTR harus **satu folder** saat dijalankan Kitchen.
- JDBC driver Oracle (`ojdbc8.jar`) taruh manual di `data-integration/lib/` Pentaho.
- VERIFY hard gate ada di KJB: hop sukses → PURGE, hop gagal → MARK_MISMATCH + ABORT.
- **WAJIB test dengan DB dummy dulu sebelum menyentuh produksi.** Mode DELETE menghapus data permanen.
- XML KJB/KTR digenerate mengikuti struktur Kettle; **verifikasi dengan buka di Spoon dulu** sebelum jalan via cron. Bila Spoon menolak parsing, laporkan errornya untuk perbaikan generator.

## Struktur Project

```
strata-app/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── schema.sql            # Fase 0 — jalankan manual di MySQL lo
└── app/
    ├── main.py           # API: connections, jobs, files, approve, download, monitor
    ├── database.py       # koneksi repo + enkripsi Fernet
    ├── dialects.py       # SQL adapter Oracle/PG/MySQL
    ├── generator.py      # builder XML .kjb/.ktr
    └── static/index.html # UI STRATA
```
