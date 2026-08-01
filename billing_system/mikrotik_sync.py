"""MikroTik Auto Sync — best-effort enable/disable based on subscription debt.

Utower logic: a subscriber whose invoices are fully paid is ENABLED on the
router; a subscriber carrying ANY unpaid debt is DISABLED (and active PPP
sessions are kicked).

This module is intentionally graceful:
  - If MIKROTIK_HOST is not configured, it logs a warning and no-ops — local
    development / setups without a router keep working.
  - Connection/API errors are caught and logged, never raised into billing.
"""
import logging

from mikrotik_api.config import load_mikrotik_config
from mikrotik_api.mikrotik_manager import MikroTikManager

log = logging.getLogger(__name__)


def _row_get(row, key, default=""):
    """Return a value from a sqlite3.Row or dict-like object."""
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, IndexError):
            return default
    return default


def get_router_config():
    """Return the MikroTik router config dict (empty host => not configured)."""
    return load_mikrotik_config()


def is_configured():
    """Return True when a MikroTik router host is configured."""
    cfg = get_router_config()
    return bool(cfg.get("host"))


def sync_mikrotik_status(mikrotik_username, should_enable):
    """Enable (should_enable=True) or disable a PPP secret on the router.

    Args:
        mikrotik_username: the PPPoE username on the router.
        should_enable: True enables the secret, False disables + kicks sessions.

    Returns:
        bool: True when the router was updated (or no-op because unconfigured),
              False when the API call failed.
    """
    if not mikrotik_username:
        log.info("[MikroTik] No PPP username — skipping sync.")
        return True  # nothing to sync; not an error

    if not is_configured():
        log.warning("[MikroTik] Router not configured (MIKROTIK_HOST empty) — skipping sync for %s",
                    mikrotik_username)
        return True  # graceful no-op

    cfg = get_router_config()
    try:
        with MikroTikManager(
            host=cfg["host"],
            username=cfg["username"],
            password=cfg["password"],
            port=cfg["port"],
        ) as router:
            if should_enable:
                router.set_ppp_secret_disabled(mikrotik_username, disabled=False)
                log.info("[MikroTik] ENABLED %s", mikrotik_username)
            else:
                router.set_ppp_secret_disabled(mikrotik_username, disabled=True)
                router.disconnect_ppp_active(mikrotik_username)
                log.info("[MikroTik] DISABLED + kicked %s", mikrotik_username)
        return True
    except Exception as e:  # noqa: BLE001 — never break billing on router issues
        log.error("[MikroTik] Sync failed for %s: %s", mikrotik_username, e)
        return False


def sync_customer_debt(customer):
    """Sync a customer's router status based on their current unpaid debt.

    Args:
        customer: a customer row dict (must include id + mikrotik_username).

    Returns:
        bool: True if sync succeeded (or was a no-op), False on router error.
    """
    import database as db
    debt = db.customer_unpaid_debt(customer["id"])
    should_enable = debt <= 0
    username = _row_get(customer, "mikrotik_username")
    return sync_mikrotik_status(username or "", should_enable)
