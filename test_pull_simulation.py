"""Simulation test: verifies that sync_pull_router pulls PPP secrets from a
mock MikroTik and upserts them into the customers table — exactly what a tower
owner's router would send on first connect.
"""
import os
import tempfile

# Point the DB to a temporary file BEFORE importing app/database.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["LOCAL_DB_PATH"] = _tmp_db.name

import database as db
from billing_system.mikrotik_sync import sync_pull_router


class FakeMikroTikManager:
    """Drop-in replacement for MikroTikManager — no real network needed."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_ppp_profiles(self):
        """Three profiles resembling real RouterOS rate-limited profiles."""
        return [
            {"name": "Standard", "rate-limit": "10M/10M"},
            {"name": "Plus", "rate-limit": "20M/20M"},
            {"name": "Turbo", "rate-limit": "50M/50M"},
        ]

    def get_ppp_secrets(self):
        """Three PPP secrets exactly as a real tower would return them."""
        return [
            {"name": "ali_ahmed", "profile": "Standard", "remote-address": "10.1.1.10", "disabled": "false"},
            {"name": "hassan_j", "profile": "Plus", "remote-address": "10.1.1.11", "disabled": "false"},
            {"name": "omar_k", "profile": "Turbo", "remote-address": "10.1.1.12", "disabled": "true"},
        ]


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  [OK]", msg)


def main():
    import unittest.mock as mock
    db.init_db()

    # Patch the MikroTikManager used inside sync_pull_router with our fake.
    with mock.patch("billing_system.mikrotik_sync.MikroTikManager", FakeMikroTikManager):
        result = sync_pull_router(host="192.168.1.1", username="admin", password="secret")

    print("=== 1) Pull summary ===")
    assert_true(result["profiles"] == 3, f"profiles fetched = {result['profiles']} (expected 3)")
    assert_true(result["secrets"] == 3, f"secrets fetched = {result['secrets']} (expected 3)")
    assert_true(result["subscribers_upserted"] == 3,
                f"subscribers upserted = {result['subscribers_upserted']} (expected 3)")
    assert_true(not result["errors"], f"no errors: {result['errors']}")

    print("=== 2) Customers created in DB ===")
    rows = db._fetchall(
        "SELECT full_name, mikrotik_username, nano_ip, status, "
        "(SELECT name FROM packages WHERE id = customers.package_id) AS pkg "
        "FROM customers WHERE mikrotik_username != '' ORDER BY mikrotik_username"
    )
    by_user = {r["mikrotik_username"]: dict(r) for r in rows}
    expected_names = {"ali_ahmed", "hassan_j", "omar_k"}
    assert_true(expected_names.issubset(set(by_user.keys())),
                f"pulled customers {sorted(by_user.keys())} include {sorted(expected_names)}")
    assert_true(len([k for k in by_user if k in expected_names]) == 3,
                f"exactly the 3 tower users exist (found {sorted(by_user.keys())})")

    ali = by_user.get("ali_ahmed")
    assert_true(ali is not None, "ali_ahmed created")
    assert_true(ali["nano_ip"] == "10.1.1.10", f"ali ip = {ali['nano_ip']}")
    assert_true(ali["status"] == "active", f"ali disabled=false → status={ali['status']} (expected active)")

    omar = by_user.get("omar_k")
    assert_true(omar is not None, "omar_k created")
    assert_true(omar["status"] == "expired", f"omar disabled=true → status={omar['status']} (expected expired)")
    assert_true(omar["pkg"] == "Turbo", f"omar package mapped from profile 'Turbo' = {omar['pkg']}")

    print("=== 3) Second pull does NOT duplicate (upsert by username) ===")
    with mock.patch("billing_system.mikrotik_sync.MikroTikManager", FakeMikroTikManager):
        result2 = sync_pull_router(host="192.168.1.1", username="admin", password="secret")
    count = db._fetchone(
        "SELECT COUNT(*) AS n FROM customers "
        "WHERE mikrotik_username IN ('ali_ahmed','hassan_j','omar_k')"
    )["n"]
    assert_true(count == 3, f"still 3 pulled customers after 2nd pull (found {count})")

    print("\n[PASSED] SIMULATION PASSED — نظام السحب يعمل 100%")


if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(_tmp_db.name)
        except OSError:
            pass