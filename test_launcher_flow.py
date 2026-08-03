"""End-to-end tests for the desktop-launcher authorization flow (Phase 14.8).

Simulates the tower EXE (IS_RENDER=False, its own local SQLite) talking to a
mocked Render cloud via /api/remote/login. Verifies:

1. Fresh cloud account (approved) → launcher /login mirrors it + succeeds.
2. Stale local pending row + cloud approved → login mirrors status/password
   and succeeds (the old "بانتظار موافقة المدير" stuck-forever bug).
3. Cloud still pending → /login returns state 'pending' (not a dead-end).
4. Cloud unreachable + local pending → /login returns state 'offline'.
5. check-status polling flips pending → approved and establishes a session.
6. Cloud 429 (rate-limit) is surfaced as offline, never a false 'pending'.
7. /api/sync/push accepts ONLY the owner token minted for that user id.

Run:
  set PYTHONIOENCODING=utf-8&& py test_launcher_flow.py
"""
import io
import json
import os
import tempfile
import unittest.mock as mock
import urllib.error as uerr
from contextlib import contextmanager

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["SQLITE_PATH"] = _tmp_db.name
os.environ["RELAY_URL"] = "https://fake-cloud.onrender.com"
os.environ["ALLOW_OPEN_REGISTRATION"] = "true"
os.environ["ACTIVATION_KEY_SECRET"] = "test-shared-activation-secret-0123456789"
os.environ["AGENT_TOKEN"] = "test-agent-token"
os.environ["COOKIE_SECURE"] = "false"

import database as db
from config import IS_RENDER, RELAY_URL
from app import app
import auth as auth_mod

FAIL = []


def assert_true(cond, msg):
    """Print OK/FAIL and collect failures without aborting mid-suite."""
    if cond:
        print(f"  [OK  ] {msg}")
    else:
        print(f"  [FAIL] {msg}")
        FAIL.append(msg)


def make_cloud_user(username="tower_admin", status="active", access_expires=None):
    """The user dict /api/remote/login returns on success."""
    return {
        "username": username,
        "full_name": "مدير البرج",
        "phone": "0770",
        "role": "agent",
        "status": status,
        "access_expires": access_expires,
    }


def ok_response(status=200, payload=None):
    """HTTP 2xx JSON response."""
    return (status, payload or {"ok": True, "state": "approved", "user": make_cloud_user()})


def err_response(status, state):
    """Non-2xx JSON response (urlopen raises HTTPError with this body)."""
    msgs = {"pending": "بانتظار موافقة المدير", "busy": "محاولات كثيرة",
            "suspended": "الحساب موقوف", "expired": "انتهت صلاحية الدخول"}
    return (status, {"ok": False, "state": state, "error": msgs.get(state, "")})


def fake_urlopen(responses, calls):
    """Build a urlopen fake returning queued (status, dict) JSON responses.

    Non-2xx statuses raise urllib.error.HTTPError carrying the JSON body, just
    like the real urllib on the cloud endpoint.
    """
    idx = [0]

    def _make_resp(status, body):
        """A response object whose methods close over status/body."""
        class _Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        _Resp.status = status
        return _Resp()

    def _inner(req, timeout=None):
        calls.append(req.full_url)
        item = responses[min(idx[0], len(responses) - 1)]
        idx[0] += 1
        if isinstance(item, Exception):
            raise item
        status, payload = item
        body = json.dumps(payload).encode("utf-8")
        if status >= 400:
            raise uerr.HTTPError(req.full_url, status, "error", {}, io.BytesIO(body))
        return _make_resp(status, body)

    return _inner


@contextmanager
def patch_cloud(responses):
    """Context manager mocking urllib.request.urlopen with queued responses.

    Yields (patcher, calls) so tests can inspect which URLs were hit. The patch
    is started by the caller and stopped automatically on exit.
    """
    calls = []
    patcher = mock.patch("urllib.request.urlopen", fake_urlopen(responses, calls))
    try:
        yield (patcher, calls)
    finally:
        patcher.stop()


def main():
    db.init_db()
    print("=" * 62)
    print("  Desktop-Launcher Authorization Flow Tests (Phase 14.8)")
    print("=" * 62)
    assert_true(IS_RENDER is False, f"IS_RENDER=False locally (got {IS_RENDER})")
    assert_true(RELAY_URL.endswith("onrender.com"), "RELAY_URL read from env")

    # ── 1) Fresh cloud account → launcher login works after approval ──
    with patch_cloud([ok_response()]) as (patcher, calls):
        patcher.start()
        try:
            c = app.test_client()
            r = c.post("/login", json={"username": "tower_admin", "password": "secret123"})
        finally:
            patcher.stop()
    d = r.get_json() or {}
    assert_true(r.status_code == 200 and d.get("ok") is True, "T1 fresh cloud login -> 200 ok")
    assert_true(d.get("state") == "approved", "T1 state=approved")
    assert_true(calls and calls[0].endswith("/api/remote/login"), "T1 cloud consulted")
    local = db.get_user_by_username("tower_admin")
    assert_true(local is not None and local["status"] == "active", "T1 account mirrored locally")
    assert_true(db.verify_password(local, "secret123"), "T1 password hash synced")
    c.post("/logout")

    # ── 2) Stale local pending row + cloud approved → login mirrors & succeeds ──
    pid = db.create_user("pending_user", "pw123", role="agent", full_name="معلق")
    db.update_user(pid, status="pending")
    cloud2 = (200, {"ok": True, "state": "approved",
                    "user": {"username": "pending_user", "full_name": "معلق",
                             "phone": "", "role": "agent", "status": "active",
                             "access_expires": None}})
    with patch_cloud([cloud2]) as (patcher, calls):
        patcher.start()
        try:
            c2 = app.test_client()
            r2 = c2.post("/login", json={"username": "pending_user", "password": "pw123"})
        finally:
            patcher.stop()
    d2 = r2.get_json() or {}
    assert_true(r2.status_code == 200 and d2.get("ok") is True,
                f"T2 pending-then-approved login -> 200 ok (got {r2.status_code})")
    updated = db.get_user_by_username("pending_user")
    assert_true(updated["status"] == "active", "T2 local status synced to active")
    assert_true(db.verify_password(updated, "pw123"), "T2 password hash synced")
    c2.post("/logout")

    # ── 3) Cloud still pending → /login returns state 'pending' ──
    with patch_cloud([err_response(403, "pending")]) as (patcher, calls):
        patcher.start()
        try:
            c3 = app.test_client()
            r3 = c3.post("/login", json={"username": "waiting_user", "password": "pw123"})
        finally:
            patcher.stop()
    d3 = r3.get_json() or {}
    assert_true(r3.status_code == 403, f"T3 pending -> 403 (got {r3.status_code})")
    assert_true(d3.get("state") == "pending", f"T3 state=pending (got {d3.get('state')})")
    assert_true(not d3.get("ok"), "T3 ok=False")
    c3.post("/logout")

    # ── 4) Cloud unreachable + local pending → /login returns state 'offline' ──
    oid = db.create_user("offline_user", "pw123", role="agent", full_name="بلا إنترنت")
    db.update_user(oid, status="pending")
    with patch_cloud([uerr.URLError("down")]) as (patcher, calls):
        patcher.start()
        try:
            c4 = app.test_client()
            r4 = c4.post("/login", json={"username": "offline_user", "password": "pw123"})
        finally:
            patcher.stop()
    d4 = r4.get_json() or {}
    assert_true(r4.status_code == 503, f"T4 offline -> 503 (got {r4.status_code})")
    assert_true(d4.get("state") == "offline", f"T4 state=offline (got {d4.get('state')})")
    c4.post("/logout")

    # ── 5) check-status polling flips pending → approved + session ──
    c5 = app.test_client()
    with patch_cloud([err_response(403, "pending"), ok_response()]) as (patcher, calls):
        patcher.start()
        try:
            r5a = c5.post("/api/auth/check-status",
                          json={"username": "poll_user", "password": "pw123"})
            r5b = c5.post("/api/auth/check-status",
                          json={"username": "poll_user", "password": "pw123"})
        finally:
            patcher.stop()
    d5a = r5a.get_json() or {}
    d5b = r5b.get_json() or {}
    assert_true(d5a.get("state") == "pending", f"T5 first poll -> pending (got {d5a.get('state')})")
    assert_true(d5b.get("ok") and d5b.get("state") == "approved",
                f"T5 second poll -> approved (got {d5b.get('state')})")
    assert_true(bool(d5b.get("user_id")), "T5 approved carries user_id")
    assert_true(bool(d5b.get("token")), "T5 approved carries a usable token")
    assert_true(c5.get("/customers").status_code == 200,
                "T5 session established by check-status (dashboard reachable)")
    c5.post("/logout")

    # ── 6) Cloud 429 rate-limit → login state 'offline', never false pending ──
    bid = db.create_user("busy_user", "pw123", role="agent", full_name="محدود")
    db.update_user(bid, status="pending")
    with patch_cloud([err_response(429, "busy")]) as (patcher, calls):
        patcher.start()
        try:
            c6 = app.test_client()
            r6 = c6.post("/login", json={"username": "busy_user", "password": "pw123"})
        finally:
            patcher.stop()
    d6 = r6.get_json() or {}
    assert_true(r6.status_code == 503, f"T6 429 -> 503 offline (got {r6.status_code})")
    assert_true(d6.get("state") == "offline", f"T6 state=offline (got {d6.get('state')})")
    c6.post("/logout")

    # ── 7) /api/sync/push accepts only the owner token for that user ──
    owner_a = db.get_user_by_id(db.create_user("push_owner_a", "pw123", role="agent"))
    owner_b = db.get_user_by_id(db.create_user("push_owner_b", "pw123", role="agent"))
    token_a = auth_mod._sign_activation(owner_a["id"], "launcher", None)
    token_b = auth_mod._sign_activation(owner_b["id"], "launcher", None)

    def push(customers, uid, token):
        client = app.test_client()
        return client.post(
            "/api/sync/push",
            json={"customers": customers, "packages": [], "user_id": uid, "token": token},
            headers={"Authorization": "Bearer test-agent-token"},
        )

    r7a = push([{"username": "mt_good", "name": "مشترك جيد", "package_name": "Standard",
                 "package_price": 50000, "subscription_status": "active"}],
               owner_a["id"], token_a)
    row_good = db._fetchone("SELECT owner_user_id FROM customers WHERE mikrotik_username='mt_good'")
    assert_true(r7a.get_json().get("ok"), "T7 valid owner token accepted")
    assert_true(row_good and row_good["owner_user_id"] == owner_a["id"],
                f"T7 customers attached to correct owner (got {row_good and row_good['owner_user_id']})")

    r7b = push([{"username": "mt_forged", "name": "مزور", "package_name": "Standard",
                 "package_price": 50000, "subscription_status": "active"}],
               owner_a["id"], token_b)
    row_bad = db._fetchone("SELECT owner_user_id FROM customers WHERE mikrotik_username='mt_forged'")
    assert_true(r7b.get_json().get("ok"), "T7 forged token request still ok (data accepted)")
    assert_true(row_bad and row_bad["owner_user_id"] is None,
                f"T7 forged token NOT attached to a wrong owner (got {row_bad and row_bad['owner_user_id']})")

    print("=" * 62)
    if FAIL:
        print(f"RESULT: {len(FAIL)} FAILURE(S)")
        for f in FAIL:
            print("   -", f)
        print("=" * 62)
        return 1
    print("ALL LAUNCHER FLOW TESTS PASSED [OK]")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    try:
        sys_code = main()
    finally:
        try:
            os.remove(_tmp_db.name)
        except OSError:
            pass
    raise SystemExit(sys_code)



