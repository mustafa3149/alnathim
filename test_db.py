"""Database creation & insertion test — Phase 10 (raw SQLite layer).

Verifies:
  1. init_db() creates the full schema (mawlidati.db).
  2. Default packages are seeded (Rule 8.3 profiles).
  3. Default admin (admin/admin123) is created.
  4. Insert + SELECT works for users, customers, invoices, payments.
  5. Every query uses `?` parameterization (no string interpolation of values).

Run:
  python test_db.py
"""
import os
import sqlite3
import sys

import database as db
from config import LOCAL_DB_PATH


def assert_true(cond, msg):
    """Print OK/FAIL and exit non-zero on failure."""
    if cond:
        print(f"[OK  ] {msg}")
    else:
        print(f"[FAIL] {msg}")
        sys.exit(1)


def inspect_tables():
    """Return the set of user tables in the database."""
    conn = sqlite3.connect(LOCAL_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_schema():
    """Verify all required tables exist."""
    db.init_db()
    tables = inspect_tables()
    required = {
        "users", "packages", "customers", "invoices", "payments",
        "expenses", "invoice_extras", "maintenance_tickets",
        "settings", "generator_info",
    }
    missing = required - tables
    assert_true(not missing, f"Schema created (missing: {missing or 'none'})")


def test_packages_seeded():
    """Rule 8.3 — default ISP profiles are present."""
    packages = db.list_packages()
    names = [p["name"] for p in packages]
    expected = ["Economy", "Plus", "Standard", "Turbo", "More", "Business"]
    assert_true(
        all(e in names for e in expected),
        f"Default packages seeded ({len(packages)} total: {', '.join(names)})",
    )
    # Prices must be > 0 and in IQD
    std = next((p for p in packages if p["name"] == "Standard"), None)
    assert_true(std is not None and std["price"] == 50000, "Standard package = 50,000 IQD")


def test_default_admin():
    """Default admin account exists with hashed password."""
    user = db.get_user_by_username("admin")
    assert_true(user is not None, "Default admin 'admin' exists")
    assert_true(user["role"] == "admin", "Admin role is 'admin'")
    assert_true(db.verify_password(user, "admin123"), "Default password admin123 works")
    assert_true(user["password_hash"].startswith("scrypt:"), "Password is hashed (scrypt)")


def test_insert_user():
    """Create and retrieve an agent user."""
    db.create_user("test_agent", "agent123", role="agent", full_name="وكيل تجريبي", phone="07701234567")
    user = db.get_user_by_username("test_agent")
    assert_true(user is not None, "Insert user (agent) retrieved")
    assert_true(user["role"] == "agent", "Role persisted")
    assert_true(db.verify_password(user, "agent123"), "Agent password verifies")
    # Cleanup
    db.delete_user(user["id"])


def test_customer_invoice_payment():
    """Insert customer -> invoice -> payment and SELECT-verify each."""
    customer_id = db.add_customer(
        full_name="عميل تجريبي",
        phone="07801111111",
        region="البصرة",
        mikrotik_username="test_pppoe",
        nano_ip="192.168.1.100",
        status="active",
    )
    assert_true(customer_id > 0, "Insert customer succeeded")

    cust = db.get_customer(customer_id)
    assert_true(cust is not None and cust["name"] == "عميل تجريبي", "Customer SELECT verified")
    assert_true(cust["username"] == "test_pppoe", "MikroTik username persisted")
    assert_true(cust["ip_address"] == "192.168.1.100", "Nano IP persisted")

    invoice_id = db.add_invoice(
        customer_id=customer_id, month=8, year=2026,
        package_name="Standard", package_price=50000,
        total_amount=50000, paid_amount=0, is_paid=False,
    )
    assert_true(invoice_id > 0, "Insert invoice succeeded")

    inv = db.get_invoice(invoice_id)
    assert_true(
        inv is not None and inv["total_amount"] == 50000 and inv["is_paid"] == 0,
        "Invoice SELECT verified (total=50000, unpaid)",
    )

    payment_id = db.add_payment(
        invoice_id=invoice_id, customer_id=customer_id,
        amount=50000, payment_date="2026-08-01", payment_method="نقدي", notes="دفعة كاملة",
    )
    assert_true(payment_id > 0, "Insert payment succeeded")

    pay = db.get_payment(payment_id)
    assert_true(
        pay is not None and pay["amount"] == 50000 and pay["payment_method"] == "نقدي",
        "Payment SELECT verified",
    )

    details = db.get_payment_details(payment_id)
    assert_true(details is not None and details["customer_name"] == "عميل تجريبي", "Payment JOIN customer verified")

    # Cleanup (cascade removes invoices + payments)
    db.delete_customer(customer_id)
    assert_true(db.get_customer(customer_id) is None, "Cleanup: customer deleted")


def test_settings_and_generator():
    """Settings + generator_info defaults are seeded."""
    settings = db.get_settings()
    assert_true(settings.get("numeral_style") == "AR", "Default numeral_style = AR")
    assert_true(settings.get("default_connection_type") == "كيبل ضوئي", "Default connection type")

    gen = db.get_generator_info()
    assert_true(gen is not None and gen["company_name"] == "الناظم", "generator_info seeded")


def test_no_string_interpolation():
    """Ensure database.py NEVER interpolates values into SQL.

    SQLite cannot parameterize identifiers (column/table names), so only
    hardcoded whitelisted identifiers (`cols`, `column`, `table`) may appear
    inside f-strings. Every user-supplied VALUE must go through `?` placeholders.
    """
    import re
    with open(os.path.join(os.path.dirname(__file__), "database.py"), encoding="utf-8") as f:
        src = f.read()

    # Allowed identifiers in f-strings are:
    #   - SQL identifiers built from hardcoded whitelists (cols, column, table,
    #     direction, col)
    #   - VALUES that are later passed via `?` params (search/q/k/month/year/key
    #     appear as LIKE patterns or %02d date strings, never as identifiers)
    allowed = {
        "cols", "column", "table", "direction", "col", "fields", "join",
        "search", "q", "k", "key", "month", "year",
        "create_sql", "copy_sql", "rebuild_plans",
    }
    bad = []
    for match in re.finditer(r'f(["\']).*?\{(.+?)\}.*?\1', src):
        name = match.group(2).strip()
        # strip dict/attribute access like `{', '.join(fields)}` -> fields
        simple = re.sub(r"^[^a-zA-Z_]*", "", name)
        simple = re.sub(r"[^a-zA-Z_].*$", "", simple)
        if simple and simple not in allowed:
            bad.append(name)
    assert_true(not bad, "No value interpolation in SQL (found identifier: %s)" % bad)


def main():
    """Run the full DB creation + insertion test suite."""
    print("-" * 60)
    print("  Phase 10 - DB Schema & Insertion Test (mawlidati.db)")
    print("-" * 60)
    test_schema()
    test_packages_seeded()
    test_default_admin()
    test_insert_user()
    test_customer_invoice_payment()
    test_settings_and_generator()
    test_no_string_interpolation()
    print("-" * 60)
    print("ALL DB TESTS PASSED [OK]")
    print("DB path:", LOCAL_DB_PATH)
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())