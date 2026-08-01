"""Load SNMP settings (community string, timeouts, OIDs).

Phase 13: first read from the DB `settings` table (saved via the "ربط اللوحة"
form), falling back to environment variables / defaults so existing .env-based
installs keep working.
"""
import os
from dotenv import load_dotenv

load_dotenv()

_DEFAULTS = {
    "community": os.getenv("SNMP_COMMUNITY", "public").strip(),
    "port": int(os.getenv("SNMP_PORT", "161") or 161),
    "timeout": float(os.getenv("SNMP_TIMEOUT", "3.0") or 3.0),
    "retries": int(os.getenv("SNMP_RETRIES", "1") or 1),
    "oid_onu_rx": os.getenv("OID_ONU_RX", "1.3.6.1.4.1.5873.4.1.2.3.1.4").strip(),
    "oid_onu_tx": os.getenv("OID_ONU_TX", "1.3.6.1.4.1.5873.4.1.2.3.1.5").strip(),
    # Walk the WHOLE Ubiquiti AirOS subtree — this is what the verified
    # probe walks to find signal (.5.1.5) and CCQ (.7.1.6). The old leaf
    # .5.1.2 points at the username string and returns no numeric signal.
    "oid_ubnt_signal": os.getenv("OID_UBNT_SIGNAL", "1.3.6.1.4.1.41112.1.4").strip(),
    "oid_ubnt_ccq": os.getenv("OID_UBNT_CCQ", "1.3.6.1.4.1.41112.1.4.7.1.6").strip(),
    "oid_mikrotik_signal": os.getenv(
        "OID_MIKROTIK_SIGNAL", "1.3.6.1.4.1.14988.1.1.2.1.1.1"
    ).strip(),
}

# Key mapping: settings-table key to config dict key.
_SETTING_KEYS = {
    "snmp_community": "community",
    "snmp_port": "port",
    "snmp_timeout": "timeout",
    "snmp_retries": "retries",
    "oid_onu_rx": "oid_onu_rx",
    "oid_onu_tx": "oid_onu_tx",
    "oid_ubnt_signal": "oid_ubnt_signal",
    "oid_ubnt_ccq": "oid_ubnt_ccq",
    "oid_mikrotik_signal": "oid_mikrotik_signal",
}


def load_snmp_config():
    """Return dict with SNMP community/port/timeouts and OID settings.

    Priority: DB `settings` table, then environment variables, then defaults.
    """
    cfg = dict(_DEFAULTS)
    try:
        import database as db
        s = db.get_settings()
        for setting_key, cfg_key in _SETTING_KEYS.items():
            value = s.get(setting_key)
            if value is None or str(value).strip() == "":
                continue
            if cfg_key in ("port", "retries"):
                try:
                    cfg[cfg_key] = int(float(str(value).strip()))
                except (ValueError, TypeError):
                    pass
            elif cfg_key == "timeout":
                try:
                    cfg[cfg_key] = float(str(value).strip())
                except (ValueError, TypeError):
                    pass
            else:
                cfg[cfg_key] = str(value).strip()
    except Exception:
        pass  # DB unavailable — fall back to env defaults
    # Phase 14.3: enforce minimums so stale DB/env values never drop timeouts
    # below the reliability floor (3.0s timeout, 1 retry).
    cfg["timeout"] = max(float(cfg.get("timeout") or 3.0), 3.0)
    cfg["retries"] = max(int(cfg.get("retries") or 1), 1)
    return cfg
