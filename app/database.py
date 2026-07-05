"""Koneksi ke MySQL Repository STRATA + enkripsi password."""
import os
import mysql.connector
from cryptography.fernet import Fernet

# --- Fernet key: dari env, atau generate & simpan ke file (dev only) ---
import tempfile
_KEY_FILE = os.path.join(
    "/app" if os.path.isdir("/app") else tempfile.gettempdir(), ".fernet_key")


def _get_key() -> bytes:
    k = os.environ.get("FERNET_KEY", "").strip()
    if k:
        return k.encode()
    if os.path.exists(_KEY_FILE):
        return open(_KEY_FILE, "rb").read()
    k = Fernet.generate_key()
    with open(_KEY_FILE, "wb") as f:
        f.write(k)
    return k


_fernet = Fernet(_get_key())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


# --- Repo connection ---
def repo_conn():
    return mysql.connector.connect(
        host=os.environ.get("REPO_HOST", "host.docker.internal"),
        port=int(os.environ.get("REPO_PORT", 3306)),
        database=os.environ.get("REPO_DB", "strata"),
        user=os.environ.get("REPO_USER", "strata"),
        password=os.environ.get("REPO_PASSWORD", "strata123"),
        autocommit=True,
    )


def q(sql, params=None, fetch=True):
    """Query helper. fetch=True → list[dict]; False → lastrowid."""
    cn = repo_conn()
    try:
        cur = cn.cursor(dictionary=True)
        cur.execute(sql, params or ())
        if fetch:
            return cur.fetchall()
        return cur.lastrowid
    finally:
        cn.close()
