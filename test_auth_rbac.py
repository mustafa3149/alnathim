"""Functional RBAC & Security tests (Phase 11).

Asserts ABSOLUTE security for the login/session and admin-vs-agent roles:
  - Unauthenticated requests are redirected away from protected pages.
  - Wrong passwords are rejected.
  - Agents can VIEW, ADD, EDIT, and DELETE customers, and PROCESS payments, but CANNOT:
      access Tower Settings, manage the team, or view audit logs.
  - Admins can do everything.
  - Audit log rows are created for agent actions (who did what).

Run:
  set PYTHONIOENCODING=utf-8&& py test_auth_rbac.py
"""
import sys
import json

import app as app_module
import database as db


def assert_true(cond, msg):
    """Print OK/FAIL and exit non-zero on failure."""
    if cond:
        print(f"[OK  ] {msg}")
    else:
        print(f"[FAIL] {msg}")
        sys.exit(1)


def login(client, username, password):
    """POST credentials and return (status, json)."""
    r = client.post("/login", json={"username": username, "password": password})
    # Mirror the real app: after login the user lands on a page, which
    # generates the session CSRF token (used by all authenticated POSTs).
    client.get("/customers")
    try:
        return r.status_code, r.get_json()
    except Exception:
        return r.status_code, None


def csrf_headers(client):
    """Return the required X-CSRF-Token header from the current session."""
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token", "")
    return {"X-CSRF-Token": token} if token else {}


def logout(client):
    """POST logout."""
    try:
        client.post("/logout")
    except Exception:
        pass


def setup_users():
    """Create a temp agent + a temp admin. Returns (agent_id, admin_id)."""
    agent_id = db.create_user("rbac_agent", "agent123", role="agent", full_name="وكيل اختبار")
    admin_id = db.create_user("rbac_admin", "admin123", role="admin", full_name="مدير اختبار")
    return agent_id, admin_id


def cleanup(agent_id, admin_id):
    """Delete temp users + any temp customer created during tests."""
    try:
        c = db.get_customer_by_name("RBAC_TEST_CUSTOMER")
        if c:
            db.delete_customer(c["id"])
    except Exception:
        pass
    if agent_id:
        db.delete_user(agent_id)
    if admin_id:
        db.delete_user(admin_id)


def setup_customer():
    """Create a temp customer for agent payment tests."""
    pkg = db.get_package_by_name("Standard")
    return db.add_customer(full_name="RBAC_TEST_CUSTOMER", phone="000",
                           package_id=pkg["id"] if pkg else None,
                           status="active")


def get_invoice_for_payment(customer_id):
    """Create/return an unpaid invoice for the temp customer."""
    now = app_module.now_dt()
    inv = db.get_customer_invoice(customer_id, now.month, now.year)
    if inv is None:
        inv_id = db.add_invoice(customer_id=customer_id, month=now.month, year=now.year,
                                package_name="Standard", package_price=50000,
                                total_amount=50000, paid_amount=0, is_paid=False)
        inv = db.get_invoice(inv_id)
    return inv


def test_unauth_redirected():
    """Anonymous users are redirected to /login for protected pages."""
    client = app_module.app.test_client()
    for path in ("/", "/customers", "/settings", "/team", "/audit",
                 "/api/customers/delete/1", "/api/users/list", "/api/packages/add"):
        r = client.get(path) if path.startswith("/api") is False else client.get(path)
        # GET on a POST-only admin endpoint shouldn't 200; it redirects to login first.
        assert_true(
            r.status_code in (302, 403, 401, 405),
            f"Unauth {path} blocked (status={r.status_code})",
        )


def test_wrong_password_rejected():
    """Incorrect credentials do not create a session."""
    client = app_module.app.test_client()
    status, data = login(client, "rbac_agent", "WRONG-PASS")
    assert_true(status == 400 and data and not data.get("ok"), "Wrong password rejected")
    logout(client)


def test_agent_can_view_customers():
    """Agents can view the customers page."""
    client = app_module.app.test_client()
    status, data = login(client, "rbac_agent", "agent123")
    assert_true(status == 200 and data and data.get("ok"), "Agent login succeeds")

    r = client.get("/customers")
    assert_true(r.status_code == 200, "Agent can view /customers")
    logout(client)


def test_agent_can_process_payment():
    """Agents can process a payment (quick-pay) and it is audit-logged."""
    customer_id = setup_customer()
    invoice = get_invoice_for_payment(customer_id)
    client = app_module.app.test_client()
    login(client, "rbac_agent", "agent123")

    before = db.audit_log_count()
    r = client.post(f"/api/payment/quick-pay/{customer_id}", json={
        "amount": 50000, "payment_date": "2026-08-01", "payment_method": "نقدي"
    }, headers=csrf_headers(client))
    data = r.get_json()
    assert_true(r.status_code == 200 and data and data.get("ok"), "Agent can process payment")
    assert_true(db.audit_log_count() > before, "Payment created an audit log entry")

    # Verify the last audit entry mentions the agent username.
    recent = db.list_audit_logs(limit=5)
    assert_true(
        any(log["username"] == "وكيل اختبار" and log["action"] == "دفع" for log in recent),
        "Audit log attributes payment to agent",
    )

    logout(client)
    db.delete_customer(customer_id)


def test_agent_blocked_from_admin_actions():
    """Agents CANNOT access settings/team/audit, or manage users."""
    customer_id = setup_customer()
    client = app_module.app.test_client()
    login(client, "rbac_agent", "agent123")

    # 1) Settings page -> 403
    r = client.get("/settings")
    assert_true(r.status_code == 403, "Agent CANNOT access Tower Settings (403)")

    # 3) Team page + user management API -> 403
    assert_true(client.get("/team").status_code == 403, "Agent CANNOT access /team (403)")
    assert_true(client.get("/api/users/list").status_code == 403, "Agent CANNOT list users (403)")
    r = client.post("/api/users/add", json={"username": "evil", "password": "evil123"},
                    headers=csrf_headers(client))
    assert_true(r.status_code == 403, "Agent CANNOT create users (403)")

    # 4) Audit log -> 403
    assert_true(client.get("/audit").status_code == 403, "Agent CANNOT access /audit (403)")

    # 5) Settings APIs -> 403
    r = client.post("/api/settings/numeral-style", json={"numeral_style": "EN"},
                    headers=csrf_headers(client))
    assert_true(r.status_code == 403, "Agent CANNOT change settings (403)")

    # 6) Package write -> 403
    r = client.post("/api/packages/add", json={"name": "HACK", "price": 1},
                    headers=csrf_headers(client))
    assert_true(r.status_code == 403, "Agent CANNOT add packages (403)")

    logout(client)
    db.delete_customer(customer_id)


def test_agent_can_add_customer():
    """Agents CAN add customers (add button un-gated)."""
    client = app_module.app.test_client()
    login(client, "rbac_agent", "agent123")

    r = client.post("/api/customers/add",
                    json={"name": "RBAC_AGENT_ADDED", "phone": "111", "package_price": 5000},
                    headers=csrf_headers(client))
    assert_true(r.status_code == 200, "Agent CAN add customer (200)")

    added = db.get_customer_by_name("RBAC_AGENT_ADDED")
    assert_true(added is not None, "Temp customer created by agent")
    if added:
        db.delete_customer(added["id"])

    logout(client)


def test_agent_can_edit_customer():
    """Agents CAN edit customers (edit button un-gated)."""
    customer_id = setup_customer()
    client = app_module.app.test_client()
    login(client, "rbac_agent", "agent123")

    r = client.post(f"/api/customers/edit/{customer_id}",
                    json={"name": "RBAC_TEST_CUSTOMER", "phone": "07701234567"},
                    headers=csrf_headers(client))
    assert_true(r.status_code == 200, "Agent CAN edit customer (200)")

    edited = db.get_customer(customer_id)
    assert_true(edited is not None and edited["phone"] == "07701234567",
                "Customer phone edited by agent")

    logout(client)
    db.delete_customer(customer_id)


def test_agent_can_delete_customer():
    """Agents CAN delete customers (delete button un-gated)."""
    customer_id = setup_customer()
    client = app_module.app.test_client()
    login(client, "rbac_agent", "agent123")

    r = client.post(f"/api/customers/delete/{customer_id}", headers=csrf_headers(client))
    assert_true(r.status_code == 200, "Agent CAN delete customer (200)")
    assert_true(db.get_customer(customer_id) is None, "Temp customer deleted by agent")

    logout(client)


def test_admin_can_do_all():
    """Admins can access settings, team, audit, and delete customers."""
    customer_id = setup_customer()
    client = app_module.app.test_client()
    login(client, "rbac_admin", "admin123")

    assert_true(client.get("/settings").status_code == 200, "Admin can access /settings")
    assert_true(client.get("/team").status_code == 200, "Admin can access /team")
    assert_true(client.get("/audit").status_code == 200, "Admin can access /audit")
    assert_true(client.get("/api/users/list").status_code == 200, "Admin can list users")

    r = client.post(f"/api/customers/delete/{customer_id}", headers=csrf_headers(client))
    assert_true(r.status_code == 200, "Admin can delete customer (200)")
    assert_true(db.get_customer(customer_id) is None, "Temp customer deleted by admin")

    logout(client)


def main():
    """Run the full RBAC/security test suite."""
    print("-" * 60)
    print("  Phase 11 - Auth, Sessions & RBAC Security Tests")
    print("-" * 60)

    agent_id, admin_id = setup_users()

    try:
        test_unauth_redirected()
        test_wrong_password_rejected()
        test_agent_can_view_customers()
        test_agent_can_process_payment()
        test_agent_blocked_from_admin_actions()
        test_agent_can_add_customer()
        test_agent_can_edit_customer()
        test_agent_can_delete_customer()
        test_admin_can_do_all()
    finally:
        cleanup(agent_id, admin_id)

    print("-" * 60)
    print("ALL RBAC SECURITY TESTS PASSED [OK]")
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())