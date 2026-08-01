"""Background Alert Worker — silently monitors online users' signal quality.

Runs indefinitely (default: every 30 minutes, configurable). In each cycle:
  1. Fetches all users from the API Gateway (`GET /api/users`).
  2. For each ONLINE user that has an IP, reads their live signal via the
     Phase 2 SNMP monitor (`SignalMonitor.get_client_signal`).
  3. If the signal drops below CRITICAL_SIGNAL_DBM, appends a timestamped
     line to a local `alerts.log` file. No popups, no intrusive UI.

Run:
  py background_worker.py          # loop forever
  py background_worker.py --once   # run a single cycle and exit (test)

Env overrides (optional):
  ALERT_INTERVAL_MINUTES  (default 30)
  ALERTS_LOG_PATH        (default ./alerts.log)
  CRITICAL_SIGNAL_DBM    (default -28.0)
  API_BASE_URL           (default http://127.0.0.1:8000)
"""
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("background_worker")

# Ensure the project root is on sys.path so `snmp_monitor` imports cleanly
# regardless of the working directory the worker is launched from.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

ALERT_INTERVAL_MINUTES = float(os.getenv("ALERT_INTERVAL_MINUTES", "30"))
ALERTS_LOG_PATH = os.getenv("ALERTS_LOG_PATH", os.path.join(_ROOT, "alerts.log"))
CRITICAL_SIGNAL_DBM = float(os.getenv("CRITICAL_SIGNAL_DBM", "-28.0"))
API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")


def _login():
    """Login to the gateway and return a fresh bearer token."""
    body = json.dumps({"password": ADMIN_PASSWORD}).encode("utf-8")
    req = urllib.request.Request(
        API_BASE_URL + "/api/login",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))["token"]


def _fetch_users():
    """Return the /api/users JSON list from the gateway (authenticated)."""
    token = _login()
    req = urllib.request.Request(
        API_BASE_URL + "/api/users",
        headers={"Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_signal_value(data):
    """Extract a numeric signal value (dBm) from a SignalMonitor dict.

    Prefers the optical RX value; falls back to the wireless signal value.
    Returns (value, kind) or (None, None) when no numeric reading exists.
    """
    if data.get("status") != "good":
        return None, None
    raw = data.get("rx_dbm")
    kind = "optical"
    if raw is None:
        raw = data.get("signal_dbm")
        kind = "wireless"
    if raw is None:
        return None, None
    try:
        return float(raw), kind
    except (TypeError, ValueError):
        return None, None


def _write_alert(username, ip, value, kind, data):
    """Append a timestamped alert line to the log file (silently)."""
    line = "[%s] ALERT user=%s ip=%s %s=%s dBm status=%s\n" % (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        username,
        ip,
        kind,
        value,
        data.get("status", "good"),
    )
    with open(ALERTS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    log.warning("Signal below threshold: %s (%s) = %.1f dBm", username, ip, value)


def run_once():
    """Run one monitoring cycle: check all online users' signals.

    Returns the number of users checked. Never raises for a single device —
    per-user exceptions are caught and logged so one bad IP never aborts
    the cycle.
    """
    users = _fetch_users()
    checked = 0
    for user in users:
        if not user.get("is_online"):
            continue
        ip = user.get("ip_address")
        if not ip:
            continue

        from snmp_monitor.signal_monitor import SignalMonitor

        try:
            monitor = SignalMonitor()
            data = monitor.get_client_signal(ip)
        except Exception as e:  # noqa: BLE001 — keep the worker alive
            log.warning("Signal check failed for %s (%s): %s", user.get("mikrotik_username"), ip, e)
            continue

        value, kind = _parse_signal_value(data)
        if value is None:
            continue

        checked += 1
        if value < CRITICAL_SIGNAL_DBM:
            _write_alert(
                user.get("mikrotik_username") or user.get("name") or "?",
                ip,
                value,
                kind,
                data,
            )

    log.info("Cycle complete: %d online users with valid signals checked.", checked)
    return checked


def main():
    """Run forever (or a single cycle with --once)."""
    once = "--once" in sys.argv

    if once:
        run_once()
        return 0

    log.info(
        "Background alert worker started — every %g minutes, "
        "threshold %.1f dBm, log %s",
        ALERT_INTERVAL_MINUTES,
        CRITICAL_SIGNAL_DBM,
        ALERTS_LOG_PATH,
    )
    while True:
        try:
            run_once()
        except Exception as e:  # noqa: BLE001 — the worker must never die
            log.error("Cycle failed: %s", e)
        try:
            time.sleep(ALERT_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            log.info("Worker stopped by user (Ctrl+C).")
            return 0


if __name__ == "__main__":
    sys.exit(main())