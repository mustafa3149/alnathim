"""Load SNMP settings (community string, timeouts, OIDs) from .env."""
import os
from dotenv import load_dotenv

load_dotenv()


def load_snmp_config():
    """Return dict with SNMP community/port/timeouts and OID settings."""
    return {
        "community": os.getenv("SNMP_COMMUNITY", "public").strip(),
        "port": int(os.getenv("SNMP_PORT", "161") or 161),
        "timeout": float(os.getenv("SNMP_TIMEOUT", "2.0") or 2.0),
        "retries": int(os.getenv("SNMP_RETRIES", "1") or 1),
        "oid_onu_rx": os.getenv("OID_ONU_RX", "1.3.6.1.4.1.5873.4.1.2.3.1.4").strip(),
        "oid_onu_tx": os.getenv("OID_ONU_TX", "1.3.6.1.4.1.5873.4.1.2.3.1.5").strip(),
        "oid_ubnt_signal": os.getenv("OID_UBNT_SIGNAL", "1.3.6.1.4.1.41112.1.4.5.1.2").strip(),
        "oid_ubnt_ccq": os.getenv("OID_UBNT_CCQ", "1.3.6.1.4.1.41112.1.4.6.1.3").strip(),
        "oid_mikrotik_signal": os.getenv(
            "OID_MIKROTIK_SIGNAL", "1.3.6.1.4.1.14988.1.1.2.1.1.1"
        ).strip(),
    }