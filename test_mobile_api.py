"""Phase 2 — Mobile API contract tests (/api/mobile/v1).

Exercises the real Flask HTTP endpoints (via test_client) with signed
bearer tokens, mirroring the existing finance/audit tests' business math:

  1. Login success → token + refresh_token + user payload.
  2. Wrong password → Arabic error envelope (does NOT use locked accounts).
  3. RBAC: agent cannot access admin-only endpoints (403 forbidden).
  4. Suspended account is rejected at login.
  5. Dashboard summary returns KPIs with a valid token.
  6. Customer create → current-month invoice with package + previous debt.
  7. Quick-pay auto-creates a current invoice when none exists.
  8. Renew carries unpaid debt into the new invoice total.
  9. Logout revokes the access token (subsequent calls → 401).
 10. Every 4xx error follows {ok:false, error:{code, message_ar}}.

Run:
  set PYTHONIOENCODING=utf-8&& py test_mobile_api.py
"""
import sys

import database as db
from app import app

MARKER = "MOB_TEST_"

PASSED = 0
FAILED = 0


def assert_true(cond, msg):
    """Print OK/FAIL, count totals, exit non-zero on any failure."""
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"[OK  ] {msg}")
    else:
        FAILED += 1
        print(f"[FAIL] {msg}")


def login(client, username, password):
    """POST /auth/login and return the parsed JSON + status code."""
    r = client.post("/api/mobile/v1/auth/login",
                    json={"username": username, "password": password})
    return r.get_json(), r.status_code


def authorized_get(client, token, path):
    """GET with a bearer token; returns (json, status)."""
    r = client.get(path, headers={"Authorization": f"Bearer {token}"})
    return r.get_json(), r.status_code


def authorized_post(client, token, path, payload=None):
    """POST with a bearer token; returns (json, status)."""
    r = client.post(path,
                    json=payload or {},
                    headers={"Authorization": f"Bearer {token}"})
    return r.get_json(), r.status_code


def cleanup():
    """Delete test customers and test users."""
    for c in db.list_customers():
        if (c["name"] or "").startswith(MARKER):
            db.delete_customer(c["id"])
    for u in db.list_users():
        if (u["username"] or "").startswith(MARKER):
            db.set_user_status(u["id"], "active")
            db._execute("DELETE FROM users WHERE id = ?", (u["id"],))


# ── Error envelope shape check ──────────────────────────────

def test_error_envelope():
    """Every 4xx error must follow {ok:false, error:{code, message_ar}}."""
    client = app.test_client()
    data, status = login(client, MARKER + "nobody", "x")
    assert_true(status == 400, f"bad login → 400 (got {status})")
    assert_true(isinstance(data, dict) and data.get("ok") is False,
                "error body has ok=False")
    err = data.get("error") or {}
    assert_true("code" in err and "message_ar" in err,
                "error has code + Arabic message_ar")


# ── Auth ────────────────────────────────────────────────────

def test_login_success():
    """Admin login returns token, refresh_token and user payload."""
    uname = MARKER + "admin"
    db.create_user(uname, "secret123", "admin", full_name="مختبر متنقل")
    try:
        client = app.test_client()
        data, status = login(client, uname, "secret123")
        assert_true(status == 200, f"login → 200 (got {status})")
        assert_true(data["ok"] is True, "login ok=True")
        payload = data["data"]
        assert_true(payload.get("token") and payload.get("refresh_token"),
                    "token + refresh_token minted")
        assert_true(payload["expires_in"] > 0, "expires_in present")
        assert_true(payload["user"]["role"] == "admin", "user.role == admin")
        assert_true(payload["user"]["username"] == uname, "username round-trips")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


def test_login_wrong_password():
    """Wrong password → Arabic invalid_credentials error, no tokens."""
    uname = MARKER + "wrongpw"
    db.create_user(uname, "secret123", "agent")
    try:
        client = app.test_client()
        data, status = login(client, uname, "badpass")
        assert_true(status == 400, f"wrong password → 400 (got {status})")
        assert_true(data["error"]["code"] == "invalid_credentials",
                    "code == invalid_credentials")
        assert_true("بيانات" in data["error"]["message_ar"],
                    "Arabic message present")
        assert_true(data.get("data") is None, "no token on failure")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


def test_login_suspended():
    """Suspended account blocked with Arabic message + 403."""
    uname = MARKER + "susp"
    db.create_user(uname, "secret123", "agent")
    try:
        user = db.get_user_by_username(uname)
        db.set_user_status(user["id"], "suspended")
        client = app.test_client()
        data, status = login(client, uname, "secret123")
        assert_true(status == 403, f"suspended → 403 (got {status})")
        assert_true(data["error"]["code"] == "suspended",
                    "code == suspended")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


def test_token_required():
    """Protected endpoints reject requests without a valid token."""
    client = app.test_client()
    data, status = authorized_get(client, "bogus-token", "/api/mobile/v1/dashboard/summary")
    assert_true(status == 401, f"bad token → 401 (got {status})")
    assert_true(data["error"]["code"] == "unauthorized", "code == unauthorized")


def test_logout_revokes():
    """After logout the access token is revoked (401 on next call)."""
    uname = MARKER + "logout"
    db.create_user(uname, "secret123", "admin")
    try:
        client = app.test_client()
        data, _ = login(client, uname, "secret123")
        token = data["data"]["token"]
        refresh = data["data"]["refresh_token"]

        out, status = authorized_post(client, token, "/api/mobile/v1/auth/logout",
                                      {"refresh_token": refresh})
        assert_true(status == 200 and out["ok"] is True, "logout succeeds")

        after, status = authorized_get(client, token, "/api/mobile/v1/dashboard/summary")
        assert_true(status == 401, f"revoked token → 401 (got {status})")
        assert_true(after["error"]["code"] == "unauthorized",
                    "revoked access rejected in mobile_login_required")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


# ── RBAC ────────────────────────────────────────────────────

def test_rbac_agent_blocked():
    """Agent token is rejected on admin-only endpoints (403 forbidden)."""
    uname = MARKER + "agent"
    db.create_user(uname, "secret123", "agent")
    try:
        client = app.test_client()
        data, _ = login(client, uname, "secret123")
        token = data["data"]["token"]

        # /audit is admin-only in the mobile blueprint.
        r = client.get("/api/mobile/v1/audit",
                       headers={"Authorization": f"Bearer {token}"})
        body = r.get_json()
        assert_true(r.status_code == 403, f"agent /audit → 403 (got {r.status_code})")
        assert_true(body["error"]["code"] == "forbidden", "code == forbidden")

        # /team list is admin-only too.
        r = client.get("/api/mobile/v1/team",
                       headers={"Authorization": f"Bearer {token}"})
        assert_true(r.status_code == 403, f"agent /team → 403 (got {r.status_code})")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


# ── Dashboard ───────────────────────────────────────────────

def test_dashboard_summary():
    """Dashboard summary returns KPI keys with a valid token."""
    uname = MARKER + "dash"
    db.create_user(uname, "secret123", "admin")
    try:
        client = app.test_client()
        data, _ = login(client, uname, "secret123")
        token = data["data"]["token"]

        body, status = authorized_get(client, token, "/api/mobile/v1/dashboard/summary")
        assert_true(status == 200 and body["ok"] is True, "dashboard 200")
        d = body["data"]
        for key in ("active_customers", "expected_income", "collected",
                    "total_debt", "expiring_this_week", "pending_tickets",
                    "recent_payments"):
            assert_true(key in d, f"dashboard key '{key}' present")
        assert_true(isinstance(d["recent_payments"], list),
                    "recent_payments is a list")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


# ── Business flow: create → quick-pay → renew (debt rollover) ─

def _make_admin_token(client):
    """Create a throwaway admin and return its bearer token."""
    uname = MARKER + "flowadmin"
    db.create_user(uname, "secret123", "admin")
    data, _ = login(client, uname, "secret123")
    return data["data"]["token"]


def test_customer_create_quickpay_renew():
    """End-to-end money math mirrors the web flow exactly."""
    token = None
    try:
        client = app.test_client()
        token = _make_admin_token(client)
        name = MARKER + "FLOW"

        # 1) Create customer with previous debt + NOT paid in full.
        body, status = authorized_post(client, token, "/api/mobile/v1/customers", {
            "name": name,
            "phone": "07701234567",
            "package_name": "Standard",
            "package_price": 50000,
            "duration_months": 1,
            "previous_debt": 100000,
            "paid_in_full": False,
        })
        assert_true(status == 200 and body["ok"] is True,
                    f"customer created (got {status})")
        cid = body["data"]["customer_id"]

        # 2) Current-month invoice total = package + previous debt.
        inv, status = authorized_get(
            client, token, f"/api/mobile/v1/payments/current-invoice/{cid}")
        assert_true(status == 200, "current-invoice endpoint 200")
        cur = inv["data"]
        assert_true(cur["invoice"] is not None, "invoice auto-created at creation")
        assert_true(cur["invoice"]["total_amount"] == 150000,
                     f"invoice total = 50k pkg + 100k debt (got {cur['invoice']['total_amount']})")
        assert_true(cur["remaining"] == 150000, "remaining == unpaid total")

        # 3) Quick-pay partial: 60,000 cash → remaining 90,000.
        body, status = authorized_post(
            client, token, f"/api/mobile/v1/quick-pay/{cid}",
            {"amount": 60000, "payment_method": "نقدي"})
        assert_true(status == 200 and body["ok"] is True, "quick-pay succeeds")
        assert_true(body["data"]["amount"] == 60000,
                    f"quick-pay amount recorded (got {body['data']['amount']})")
        remaining_after_pay = body["data"]["invoice"]["remaining"]
        assert_true(remaining_after_pay == 90000,
                    f"remaining after 60k pay = 90k (got {remaining_after_pay})")

        # 4) Renew 1 month → new invoice carries the remaining 90k debt.
        body, status = authorized_post(
            client, token, f"/api/mobile/v1/customers/{cid}/renew",
            {"months": 1})
        assert_true(status == 200 and body["ok"] is True, f"renew succeeds (got {status})")
        renewed_total = body["data"]["total_amount"]
        assert_true(renewed_total == 50000 + 90000,
                     f"renew total = 50k + carried 90k (got {renewed_total})")
        assert_true(body["data"]["carried_debt"] == 90000,
                    "carried_debt == 90k on renew")

        # 5) Debts list reflects the post-merge remaining balance.
        #    After renew merges carried debt into the existing invoice, the
        #    new total is 140k (50k pkg + 90k carried) and the 60k payment
        #    stays credited → remaining debt = 80k.
        debts, status = authorized_get(client, token, "/api/mobile/v1/debts")
        assert_true(status == 200, "debts endpoint 200")
        mine = [x for x in debts["data"]["items"] if x["customer_id"] == cid]
        assert_true(len(mine) == 1, "customer appears in debts list")
        assert_true(mine[0]["total_debt"] == 80000,
                    f"debt after merge = 140k - 60k paid = 80k (got {mine[0]['total_debt']})")
    finally:
        # Cleanup in reverse order (token first, then DB rows).
        if token:
            try:
                client.post("/api/mobile/v1/auth/logout",
                            headers={"Authorization": f"Bearer {token}"})
            except Exception:
                pass
        cleanup()


# ── Reports ─────────────────────────────────────────────────

def test_report():
    """Monthly report returns finance keys."""
    uname = MARKER + "report"
    db.create_user(uname, "secret123", "admin")
    try:
        client = app.test_client()
        data, _ = login(client, uname, "secret123")
        token = data["data"]["token"]
        body, status = authorized_get(client, token, "/api/mobile/v1/report")
        assert_true(status == 200 and body["ok"] is True, "report endpoint 200")
        d = body["data"]
        for key in ("expected", "collected", "expenses_total", "net_profit",
                    "unpaid_count", "total_invoices"):
            assert_true(key in d, f"report key '{key}' present")
    finally:
        db._execute("DELETE FROM users WHERE username = ?", (uname,))


# ── Runner ──────────────────────────────────────────────────

if __name__ == "__main__":
    cleanup()
    test_error_envelope()
    test_login_success()
    test_login_wrong_password()
    test_login_suspended()
    test_token_required()
    test_logout_revokes()
    test_rbac_agent_blocked()
    test_dashboard_summary()
    test_customer_create_quickpay_renew()
    test_report()
    cleanup()

    print(f"\nMobile API contract tests: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)