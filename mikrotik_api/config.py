"""Load MikroTik connection settings from the DB settings table (or .env).

Phase 13: credentials saved through the "ربط اللوحة" settings form are stored
in the `settings` table. This loader checks the DB first and falls back to
environment variables so existing .env-based installs keep working and the
billing auto-sync uses the same credentials the admin saved in the UI.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def load_mikrotik_config():
    """Return dict with host/user/password/port.

    Priority: DB `settings` table → environment variables → defaults.
    """
    cfg = {
        "host": os.getenv("MIKROTIK_HOST", "").strip(),
        "username": os.getenv("MIKROTIK_USER", "admin").strip(),
        "password": os.getenv("MIKROTIK_PASSWORD", ""),
        "port": int(os.getenv("MIKROTIK_PORT", "8728") or 8728),
        "ssl": os.getenv("MIKROTIK_SSL", "0").strip() in ("1", "true", "yes"),
    }
    try:
        import database as db
        s = db.get_settings()
        if s.get("mikrotik_host", "").strip():
            cfg["host"] = s["mikrotik_host"].strip()
        if s.get("mikrotik_user", "").strip():
            cfg["username"] = s["mikrotik_user"].strip()
        if s.get("mikrotik_password", "") is not None:
            cfg["password"] = s.get("mikrotik_password", "")
        try:
            cfg["port"] = int(s.get("mikrotik_port", cfg["port"]) or cfg["port"])
        except (ValueError, TypeError):
            pass
        cfg["ssl"] = s.get("mikrotik_ssl", "0").strip() in ("1", "true", "yes")
    except Exception:
        pass  # DB unavailable — fall back to env defaults
    return cfg
