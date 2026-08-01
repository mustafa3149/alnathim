"""Test SNMP signal monitoring.

Run:  cd snmp_monitor && python -m test_snmp [--ip x.x.x.x] [--type auto]
It pulls the first active client IP from Phase 1 (mikrotik_api) if available,
otherwise demonstrates the graceful offline/timeout path with a dummy IP.
"""
import json
import sys

from config import load_snmp_config
from signal_monitor import SignalMonitor


def get_first_active_ip():
    """Reuse Phase 1 to obtain a live client IP, or None."""
    try:
        import sys as _sys
        sys.path.insert(0, "..")  # allow importing mikrotik_api from repo root
        from mikrotik_api import MikroTikManager
        from mikrotik_api.config import load_mikrotik_config

        cfg = load_mikrotik_config()
        if not cfg["host"]:
            return None
        with MikroTikManager(cfg["host"], cfg["username"], cfg["password"], port=cfg["port"]) as m:
            clients = m.get_active_clients_with_ip()
            for c in clients:
                if c.get("address"):
                    return c["address"]
    except Exception:
        return None
    return None


def main():
    cfg = load_snmp_config()
    monitor = SignalMonitor()

    ip = None
    if "--ip" in sys.argv:
        ip = sys.argv[sys.argv.index("--ip") + 1]
    if not ip:
        ip = get_first_active_ip()
    if not ip:
        ip = "192.0.2.1"  # TEST-NET dummy → guaranteed offline/timeout

    dtype = "auto"
    if "--type" in sys.argv:
        dtype = sys.argv[sys.argv.index("--type") + 1]

    print("Monitoring IP: %s (type=%s, community=%s)" % (ip, dtype, cfg["community"]))
    result = monitor.get_client_signal(ip, device_type=dtype)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result.get("status") == "offline/timeout":
        print("OK: graceful offline/timeout path works (no crash).")
    else:
        print("Signal reading received from %s." % ip)
    return 0


if __name__ == "__main__":
    sys.exit(main())