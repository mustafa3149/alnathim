"""Sync engine — reconcile subscriber expiry with MikroTik PPP state (Phase 3).

Logic per subscriber:
  - expiry_date < today (strictly)  -> disabled=yes on /ppp secret + disconnect /ppp active
  - expiry_date >= today            -> disabled=no  on /ppp secret

The MikroTik manager is injectable (for tests). If not provided, a real
MikroTikManager is built lazily from mikrotik_api.config.
"""
import os
import sys
from datetime import date, datetime

from .database import get_connection, get_all_subscribers

_BILLING_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_BILLING_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)


def _build_mikrotik_manager():
    """Construct a real, connected MikroTikManager from the Phase 1 config."""
    from mikrotik_api.config import load_mikrotik_config
    from mikrotik_api.mikrotik_manager import MikroTikManager

    cfg = load_mikrotik_config()
    manager = MikroTikManager(
        cfg["host"],
        cfg["username"],
        cfg["password"],
        port=cfg["port"],
    )
    manager.connect()
    return manager


def _parse_date(value):
    """Parse an ISO date string (YYYY-MM-DD) into a datetime.date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def _update_status(db_path, subscriber_id, status):
    """Persist the computed status column for a subscriber."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            "UPDATE Subscribers SET status = ? WHERE id = ?",
            (status, subscriber_id),
        )
        conn.commit()
    finally:
        conn.close()


def sync_mikrotik_status(mikrotik=None, db_path=None, dry_run=False):
    """Sync every subscriber's PPP state to their expiry date.

    Args:
        mikrotik: a MikroTikManager-like object exposing
                  set_ppp_secret_disabled(username, disabled) and
                  disconnect_ppp_active(username). Defaults to a real
                  manager built from environment config.
        db_path: optional override for the database path.
        dry_run: if True, only report intended actions — never touch the router.

    Returns:
        Summary dict with counts:
          checked, expired, active, disabled, enabled, missing, errors.
    """
    manager = mikrotik
    if manager is None and not dry_run:
        manager = _build_mikrotik_manager()

    today = date.today()
    subscribers = get_all_subscribers(db_path=db_path)

    summary = {
        "checked": len(subscribers),
        "expired": 0,
        "active": 0,
        "disabled": 0,
        "enabled": 0,
        "missing": 0,
        "errors": [],
    }

    for sub in subscribers:
        username = sub["mikrotik_username"]
        expiry = _parse_date(sub["expiry_date"])
        is_expired = expiry < today

        if is_expired:
            summary["expired"] += 1
            new_status = "expired"
        else:
            summary["active"] += 1
            new_status = "active"

        if sub["status"] != new_status:
            _update_status(db_path, sub["id"], new_status)

        if dry_run:
            action = "DISABLE + disconnect" if is_expired else "ENABLE"
            print("[dry-run] %-25s -> %s (expiry=%s)" % (username, action, sub["expiry_date"]))
            continue

        try:
            if is_expired:
                found = manager.set_ppp_secret_disabled(username, True)
                if not found:
                    summary["missing"] += 1
                else:
                    summary["disabled"] += 1
                manager.disconnect_ppp_active(username)
            else:
                found = manager.set_ppp_secret_disabled(username, False)
                if not found:
                    summary["missing"] += 1
                else:
                    summary["enabled"] += 1
        except Exception as e:  # noqa: BLE001 — report per-subscriber, keep going
            summary["errors"].append({"username": username, "error": str(e)})

    return summary