"""Demonstrate the Billing System (Phase 3) safely.

Run:  cd billing_system && python -m test_billing

Safety:
  * Uses a temporary database (never the real isp_billing.db).
  * Uses a FakeMikroTik instead of a real router — the sync engine's
    actions are recorded and asserted, and NO real user is ever touched.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

# Make sure the project root is on sys.path so `import billing_system`
# works when this file is run directly from anywhere.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from billing_system import (  # noqa: E402
    add_subscriber,
    get_all_subscribers,
    get_subscriber,
    init_db,
    process_payment,
    sync_mikrotik_status,
)
from billing_system.database import get_connection  # noqa: E402

# Force UTF-8 on stdout so Arabic test names print correctly on Windows consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


class FakeMikroTik:
    """Records sync actions instead of talking to a real router."""

    def __init__(self):
        self.disable_calls = []
        self.enable_calls = []
        self.disconnect_calls = []

    def set_ppp_secret_disabled(self, username, disabled):
        if disabled:
            self.disable_calls.append(username)
        else:
            self.enable_calls.append(username)
        return True  # pretend every user exists on the router

    def disconnect_ppp_active(self, username):
        self.disconnect_calls.append(username)


def _count_payments(db_path):
    """Return the number of payment rows in the test DB."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM Payments").fetchone()
        return row["n"]
    finally:
        conn.close()


def main():
    tmpdir = tempfile.mkdtemp(prefix="billing_test_")
    db_path = os.path.join(tmpdir, "test_isp_billing.db")
    fake = FakeMikroTik()

    print("=== Step 1: create DB + tables on a temp path ===")
    init_db(db_path)
    print("DB created at:", db_path)

    print("\n=== Step 2: add a dummy subscriber (safe username) ===")
    dummy_user = "dummy-qa-%s" % os.getpid()
    future = date.today() + timedelta(days=30)
    add_subscriber(
        name="مستخدم تجريبي (QA)",
        mikrotik_username=dummy_user,
        monthly_fee=50000,
        expiry_date=future,
        status="active",
        db_path=db_path,
    )

    sub = get_subscriber(dummy_user, db_path=db_path)
    assert sub is not None, "get_subscriber returned None for dummy user"
    assert sub["mikrotik_username"] == dummy_user
    print("added:", sub)
    print("all subscribers:", len(get_all_subscribers(db_path=db_path)))

    print("\n=== Step 3a: process payment on an ACTIVE dummy ===")
    before = _count_payments(db_path)
    result = process_payment(dummy_user, amount=50000, days_to_add=30, db_path=db_path)
    assert _count_payments(db_path) == before + 1, "payment row was not logged"
    assert result["expiry_date"] == (future + timedelta(days=30)).isoformat()
    assert result["status"] == "active"
    print("payment result:", result)

    print("\n=== Step 3b: process payment on an EXPIRED dummy (renew from today) ===")
    expired_user = "dummy-expired-%s" % os.getpid()
    yesterday = date.today() - timedelta(days=1)
    add_subscriber(
        name="مستخدم منتهي (QA)",
        mikrotik_username=expired_user,
        monthly_fee=45000,
        expiry_date=yesterday,
        status="expired",
        db_path=db_path,
    )
    result = process_payment(expired_user, amount=45000, days_to_add=30, db_path=db_path)
    expected = (date.today() + timedelta(days=30)).isoformat()
    assert result["expiry_date"] == expected, "expired subscriber should renew from today"
    assert result["status"] == "active"
    print("renewal from today:", result)

    print("\n=== Step 4a: sync engine — expired dummy gets disabled+disconnected ===")
    # Create one more subscriber that is currently expired and on the router.
    kick_user = "dummy-kick-%s" % os.getpid()
    add_subscriber(
        name="مستخدم للحذف (QA)",
        mikrotik_username=kick_user,
        monthly_fee=40000,
        expiry_date=yesterday,
        status="expired",
        db_path=db_path,
    )

    summary = sync_mikrotik_status(mikrotik=fake, db_path=db_path)
    print("summary:", summary)
    assert summary["checked"] >= 1
    assert kick_user in fake.disable_calls, "expired user was not disabled"
    assert kick_user in fake.disconnect_calls, "expired user was not disconnected"
    assert dummy_user in fake.enable_calls, "active user was not enabled"

    print("\n=== Step 4b: sync engine — after payment, user is enabled ===")
    process_payment(kick_user, amount=40000, days_to_add=30, db_path=db_path)
    fake.disable_calls.clear()
    fake.disconnect_calls.clear()
    fake.enable_calls.clear()

    summary2 = sync_mikrotik_status(mikrotik=fake, db_path=db_path)
    print("summary after payment:", summary2)
    assert kick_user not in fake.disable_calls, "paid user should not be disabled"
    assert kick_user not in fake.disconnect_calls
    assert kick_user in fake.enable_calls, "paid user should be enabled"

    print("\n=== Step 5: dry-run mode does not touch the router ===")
    fake.disable_calls.clear()
    fake.enable_calls.clear()
    fake.disconnect_calls.clear()
    summary3 = sync_mikrotik_status(mikrotik=fake, db_path=db_path, dry_run=True)
    assert fake.disable_calls == [] and fake.enable_calls == []
    assert fake.disconnect_calls == []
    print("dry-run summary:", summary3)

    print("\n✅ ALL BILLING SYSTEM TESTS PASSED")
    print("(used fake router — no real MikroTik user was modified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())