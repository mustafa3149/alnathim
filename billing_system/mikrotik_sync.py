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
from datetime import datetime, timedelta

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


def sync_pull_router(dry_run=False):
    """Pull all /ppp secret + profiles from the router and upsert into cloud DB.

    Hybrid pull (Local Laptop/PC): this function connects to the MikroTik router
    on the LAN, reads every PPP secret (name, profile, remote-address, disabled),
    auto-creates missing packages via `auto_mapper`, and upserts subscribers into
    the local/cloud database.

    Note: For rendering, callers must first pull the local DB snapshot then push
    it to Render via `/api/sync/push` (implemented at the app layer).

    Args:
        dry_run: when True, log actions without writing to the database.

    Returns:
        dict summary {profiles, secrets, packages_created, subscribers_upserted,
                      errors}.
    """
    import database as db
    from mikrotik_api.auto_mapper import ensure_package_from_profile
    from mikrotik_api.config import load_mikrotik_config

    cfg = load_mikrotik_config()
    summary = {"profiles": 0, "secrets": 0, "packages_created": 0,
               "subscribers_upserted": 0, "errors": []}
    if not cfg.get("host"):
        log.warning("[MikroTik Pull] Router not configured — no-op")
        return summary

    try:
        with MikroTikManager(
            host=cfg["host"],
            username=cfg["username"],
            password=cfg["password"],
            port=cfg["port"],
        ) as router:
            # 1) Pull profiles to auto-create packages.
            profiles = router.get_ppp_profiles()
            summary["profiles"] = len(profiles)
            for prof in profiles:
                name = (prof or {}).get("name", "").strip()
                if not name:
                    continue
                if not dry_run:
                    pkg_id = ensure_package_from_profile(
                        name, rate_limit=(prof or {}).get("rate-limit", "")
                    )
                    if pkg_id:
                        summary["packages_created"] += 1

            # 2) Pull PPP secrets and upsert customers.
            secrets = router.get_ppp_secrets()
            summary["secrets"] = len(secrets)
            for sec in secrets:
                username = (sec.get("name") or "").strip()
                if not username:
                    continue
                profile_name = (sec.get("profile") or "").strip()
                remote_ip = (sec.get("remote-address") or "").strip()
                disabled = (sec.get("disabled") or "").strip() in ("true", "yes")

                # Map profile -> package id.
                pkg = None
                if profile_name:
                    pkg = ensure_package_from_profile(profile_name, rate_limit="")
                    if dry_run:
                        log.info("[Pull] profile=%s -> pkg=%s", profile_name, pkg)

                # Find existing customer by mikrotik_username.
                existing = None
                for c in db.list_active_customers():
                    if (c.get("username") or "").strip() == username:
                        existing = c
                        break
                if existing is None:
                    total_rows = db._fetchall("SELECT id FROM customers WHERE mikrotik_username = ?", (username,))
                    if total_rows:
                        existing = db.get_customer(total_rows[0]["id"])

                status = "expired" if disabled else "active"
                now = datetime.now().replace(second=0, microsecond=0)
                renewal = now + timedelta(days=31)
                if existing:
                    if not dry_run:
                        db.update_customer(
                            existing["id"],
                            ip_address=remote_ip,
                            package_id=pkg,
                            status=status,
                            subscription_status=status,
                            renewal_date=renewal.strftime("%Y-%m-%d %H:%M"),
                        )
                        summary["subscribers_upserted"] += 1
                else:
                    if not dry_run:
                        db.add_customer(
                            full_name=username,
                            phone="",
                            region="",
                            package_id=pkg,
                            mikrotik_username=username,
                            mikrotik_password="",
                            nano_ip=remote_ip,
                            device_type="نانو" if not remote_ip else "كيبل ضوئي",
                            subscription_date=now.strftime("%Y-%m-%d %H:%M"),
                            renewal_date=renewal.strftime("%Y-%m-%d %H:%M"),
                            status=status,
                        )
                        summary["subscribers_upserted"] += 1
                    else:
                        log.info("[Pull] would create subscriber %s", username)
    except Exception as e:  # noqa: BLE001 — never break the app on router issues
        log.error("[MikroTik Pull] Error: %s", e)
        summary["errors"].append(str(e))
    return summary


def sync_push_cloud(payload=None):
    """Push the local DB (or a provided payload) to the Render cloud via API.

    Hybrid upload (Local Laptop/PC): the user runs this locally (e.g. from a
    script or the settings page) to forward the entire local snapshot to the
    Render endpoint `/api/sync/push` so mobile/web remote views see it instantly.

    Args:
        payload: optional dict of subscribers/packages to push. When None the
            local DB is read fully (customers + packages + current invoices).

    Returns:
        dict with {ok, sent_customers, sent_packages} or raises on failure.
    """
    import json
    import urllib.request

    import database as db
    from config import RELAY_URL, AGENT_TOKEN

    if not RELAY_URL:
        log.warning("[SyncPush] RELAY_URL not set — cannot push to cloud")
        return {"ok": False, "error": "RELAY_URL غير مضبوط"}

    if payload is None:
        customers = []
        for c in db.export_customers():
            customers.append({
                "id": c["id"],
                "name": c["name"],
                "phone": c["phone"] or "",
                "phone2": c["phone2"] or "",
                "whatsapp_phone": c["whatsapp_phone"] or "",
                "address": c["address"] or "",
                "region": c["region"] or "",
                "package_name": c["package_name"] or "",
                "package_price": c["package_price"] or 0,
                "username": c["username"] or "",
                "password": c["password"] or "",
                "ip_address": c["ip_address"] or "",
                "device_type": c["device_type"] or "",
                "subscription_status": c["subscription_status"] or "active",
                "renewal_date": c["renewal_date"] or "",
                "notes": c["notes"] or "",
            })
        packages = [
            {"id": p["id"], "name": p["name"], "price": p["price"], "speed": p["speed"] or ""}
            for p in db.list_packages()
        ]
        payload = {"customers": customers, "packages": packages}

    body = json.dumps(payload).encode("utf-8")
    url = RELAY_URL.rstrip("/") + "/api/sync/push"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + AGENT_TOKEN},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8", "replace"))
    log.info("[SyncPush] Sent %d customers, %d packages -> %s",
             len(payload.get("customers", [])), len(payload.get("packages", [])), url)
    return result
