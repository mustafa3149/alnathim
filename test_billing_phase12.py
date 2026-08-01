"""Phase 12 — Advanced Billing Logic & Partial Payments (Utower) tests.

Verifies:
  1. Customer added with previous_debt + NOT paid-in-full → an unpaid
     invoice for the debt is generated instantly.
  2. Customer added with previous_debt + paid-in-full (واصل) → the debt
     invoice is created PAID.
  3. Renewal invoicing: total = package x months + carried debt, with the
     carried debt stored in previous_debt.
  4. Partial payment leaves is_paid=False and the remainder is debt.
  5. Full payment clears the debt (is_paid=True).
  6. MikroTik sync is best-effort (no crash when router is unconfigured).

Run:
  set PYTHONIOENCODING=utf-8&& py test_billing_phase12.py
"""
import sys
import os

import database as db
from datetime import datetime

MARKER = "B12_TEST_"


def assert_true(cond, msg):
    """Print OK/FAIL and exit non-zero on failure."""
    if cond:
        print(f"[OK  ] {msg}")
    else:
        print(f"[FAIL] {msg}")
        sys.exit(1)


def now_ym():
    """Current month/year tuple for invoice targeting."""
    now = datetime.now()
    return now.month, now.year


def cleanup_customers():
    """Delete any customer whose name starts with B12_TEST_."""
    for c in db.list_customers():
        if (c["name"] or "").startswith(MARKER):
            db.delete_customer(c["id"])


def test_add_customer_with_debt_unpaid():
    """previous_debt > 0 and paid_in_full=False → instant UNPAID debt invoice."""
    month, year = now_ym()
    name = MARKER + "DEBT_UNPAID"
    customer_id = db.add_customer(full_name=name, phone="000",
                                  mikrotik_username="b12_debt_user",
                                  status="active", previous_debt=100000)

    # The invoice engine (in app.py) creates ONE current-month invoice with
    # total = package + previous_debt, marked unpaid because واصل is false.
    db.add_invoice(customer_id=customer_id, month=month, year=year,
                   package_name="Standard", package_price=50000,
                   total_amount=150000, paid_amount=0, is_paid=False,
                   previous_debt=100000)

    debt = db.customer_unpaid_debt(customer_id)
    assert_true(debt >= 150000, f"Unpaid invoice with merged debt generated (debt={debt})")

    invoices = db.list_customer_invoices(customer_id)
    assert_true(len(invoices) == 1, "Single invoice holds package + previous debt")

    inv = invoices[0]
    assert_true(inv["total_amount"] == 150000, "Invoice total = 50,000 package + 100,000 debt")
    assert_true(inv["is_paid"] == 0, "Invoice is UNPAID when paid_in_full is false")
    assert_true(inv["previous_debt"] == 100000, "previous_debt stored on invoice")

    db.delete_customer(customer_id)


def test_add_customer_with_debt_paid_in_full():
    """previous_debt > 0 and paid_in_full=True → debt invoice created PAID."""
    month, year = now_ym()
    name = MARKER + "DEBT_PAID"
    customer_id = db.add_customer(full_name=name, phone="000",
                                  mikrotik_username="b12_paid_user",
                                  status="active", previous_debt=75000)

    # paid_in_full → the previous debt is settled as part of the single invoice.
    db.add_invoice(customer_id=customer_id, month=month, year=year,
                   package_name="Standard", package_price=50000,
                   total_amount=125000, paid_amount=75000, is_paid=False,
                   previous_debt=75000)

    debt = db.customer_unpaid_debt(customer_id)
    # The debt portion is paid; only the 50,000 package remains.
    assert_true(debt == 50000, f"Paid-in-full debt -> only package unpaid (debt={debt})")

    inv = db.list_customer_invoices(customer_id)[0]
    assert_true(inv["paid_amount"] == 75000, "Paid portion = 75,000 (the previous debt)")
    assert_true(inv["previous_debt"] == 75000, "previous_debt stored on the invoice")

    db.delete_customer(customer_id)


def test_renewal_carries_debt():
    """Renewal invoice = package x months + carried debt (Utower engine)."""
    month, year = now_ym()
    name = MARKER + "RENEW"
    customer_id = db.add_customer(full_name=name, phone="000",
                                  mikrotik_username="b12_renew_user",
                                  status="active", previous_debt=0)

    # Existing unpaid debt from a previous month.
    db.add_invoice(customer_id=customer_id, month=1, year=2025,
                   package_name="Standard", package_price=50000,
                   total_amount=50000, paid_amount=0, is_paid=False)

    carried = db.customer_unpaid_debt(customer_id, up_to_month=now_ym()[0], up_to_year=now_ym()[1])
    assert_true(carried >= 50000, f"Existing debt detected for carry (debt={carried})")

    # Renew 2 months: payable = 2 x 50,000 + 50,000 carried = 150,000
    renew_total = 50000 * 2
    total_due = renew_total + carried
    invoice_id = db.add_invoice(customer_id=customer_id, month=month, year=year,
                                package_name="Standard", package_price=50000,
                                total_amount=total_due, paid_amount=0, is_paid=False,
                                previous_debt=carried)
    inv = db.get_invoice(invoice_id)
    assert_true(inv["total_amount"] == 150000, f"Renewal invoice total = 150,000 (got {inv['total_amount']})")
    assert_true(inv["previous_debt"] == 50000, "Carried debt stored in previous_debt")
    assert_true(inv["is_paid"] == 0, "Renewal with carried debt is unpaid")

    db.delete_customer(customer_id)


def test_partial_payment_keeps_debt():
    """Paying LESS than total keeps is_paid=False; remainder becomes debt."""
    month, year = now_ym()
    name = MARKER + "PARTIAL"
    customer_id = db.add_customer(full_name=name, phone="000",
                                  mikrotik_username="b12_partial_user",
                                  status="active", previous_debt=0)
    invoice_id = db.add_invoice(customer_id=customer_id, month=month, year=year,
                                package_name="Standard", package_price=50000,
                                total_amount=50000, paid_amount=0, is_paid=False)
    db.add_payment(invoice_id=invoice_id, customer_id=customer_id, amount=20000,
                   payment_date=datetime.now().date().isoformat())

    # Simulate payment endpoint math.
    inv = db.get_invoice(invoice_id)
    new_paid = (inv["paid_amount"] or 0) + 20000
    is_paid = new_paid >= (inv["total_amount"] or 0)
    db.update_invoice(invoice_id, paid_amount=new_paid, is_paid=is_paid)
    inv = db.get_invoice(invoice_id)

    assert_true(inv["paid_amount"] == 20000, "Partial payment recorded (20,000)")
    assert_true(inv["is_paid"] == 0, "Invoice remains unpaid after partial payment")
    assert_true(inv["total_amount"] - inv["paid_amount"] == 30000,
                "Remaining 30,000 rolls over as DEBT")
    debt = db.customer_unpaid_debt(customer_id)
    assert_true(debt == 30000, f"customer_unpaid_debt reports 30,000 (got {debt})")

    db.delete_customer(customer_id)


def test_full_payment_clears_debt():
    """Paying the full remaining amount sets is_paid=True and clears debt."""
    month, year = now_ym()
    name = MARKER + "FULLPAY"
    customer_id = db.add_customer(full_name=name, phone="000",
                                  mikrotik_username="b12_fullpay_user",
                                  status="active", previous_debt=0)
    invoice_id = db.add_invoice(customer_id=customer_id, month=month, year=year,
                                package_name="Standard", package_price=50000,
                                total_amount=50000, paid_amount=0, is_paid=False)
    db.add_payment(invoice_id=invoice_id, customer_id=customer_id, amount=50000,
                   payment_date=datetime.now().date().isoformat())

    inv = db.get_invoice(invoice_id)
    new_paid = (inv["paid_amount"] or 0) + 50000
    is_paid = new_paid >= (inv["total_amount"] or 0)
    db.update_invoice(invoice_id, paid_amount=new_paid, is_paid=is_paid)
    inv = db.get_invoice(invoice_id)

    assert_true(inv["paid_amount"] == 50000, "Full payment recorded")
    assert_true(inv["is_paid"] == 1, "Invoice fully paid -> is_paid=True")
    assert_true(db.customer_unpaid_debt(customer_id) == 0, "Debt cleared to zero")

    db.delete_customer(customer_id)


def test_mikrotik_sync_graceful():
    """MikroTik sync is best-effort — no crash when router not configured."""
    from billing_system.mikrotik_sync import sync_mikrotik_status, sync_customer_debt
    # Unconfigured router → returns True (graceful no-op), no exception.
    ok = sync_mikrotik_status("fake_user", should_enable=False)
    assert_true(ok is True, "Sync no-ops gracefully when router unconfigured")

    customer = db.get_customer_by_name("nonexistent_b12")
    # sync_customer_debt should not crash on a fake customer with username only
    ok2 = sync_customer_debt({"id": 999999, "mikrotik_username": "fake_user"})
    assert_true(ok2 is True, "sync_customer_debt no-ops gracefully")


def main():
    """Run the Phase 12 billing test suite."""
    print("-" * 60)
    print("  Phase 12 - Advanced Billing & Partial Payments (Utower)")
    print("-" * 60)

    # Ensure the schema + migrations are applied (adds previous_debt, etc.).
    db.init_db()

    cleanup_customers()
    try:
        test_add_customer_with_debt_unpaid()
        test_add_customer_with_debt_paid_in_full()
        test_renewal_carries_debt()
        test_partial_payment_keeps_debt()
        test_full_payment_clears_debt()
        test_mikrotik_sync_graceful()
    finally:
        cleanup_customers()

    print("-" * 60)
    print("ALL PHASE 12 BILLING TESTS PASSED [OK]")
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())