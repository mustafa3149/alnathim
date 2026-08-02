"""Verifies the local→cloud registration forwarding:
when the app runs on a desktop EXE (IS_RENDER=False) and RELAY_URL is set,
POST /register must forward the payload to the cloud /register endpoint.
"""
import json
import os
import tempfile
import unittest.mock as mock

# Temp DB before importing app.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["SQLITE_PATH"] = _tmp_db.name     # config reads SQLITE_PATH, not LOCAL_DB_PATH
os.environ["RELAY_URL"] = "https://alnathim.onrender.com"
os.environ["ALLOW_OPEN_REGISTRATION"] = "true"

import database as db
from config import IS_RENDER, RELAY_URL
from auth import auth_bp
from app import app

assert_true = lambda cond, msg: (print("  [OK]", msg) if cond else (_ for _ in ()).throw(AssertionError(msg)))

def main():
    db.init_db()
    print("IS_RENDER:", IS_RENDER, "(expected False on local/EXE)")
    assert_true(IS_RENDER is False, f"IS_RENDER=False locally (got {IS_RENDER})")
    assert_true(RELAY_URL == "https://alnathim.onrender.com", "RELAY_URL is read from env")

    c = app.test_client()

    # Intercept the outgoing HTTP call: capture URL + payload, return ok.
    _captured = {}

    class _FakeResp:
        def read(self):
            return json.dumps({"ok": True, "message": "تم إرسال طلب التسجيل — بانتظار موافقة المدير"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(req, timeout=None):
        _captured["url"] = req.full_url
        _captured["method"] = req.get_method()
        _captured["body"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp()

    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        r = c.post("/register", json={
            "full_name": "اختبار توجيه", "username": "tower_test",
            "password": "secret12", "phone": "0770", "invite_code": "",
        })

    print("=== Forward check ===")
    assert_true(_captured.get("url", "").endswith("/register"),
                f"forwarded to RELAY_URL/register: {_captured.get('url')}")
    assert_true(_captured.get("method") == "POST", f"HTTP method = {_captured.get('method')}")
    body = _captured.get("body", {})
    assert_true(body.get("username") == "tower_test", f"username forwarded = {body.get('username')}")
    assert_true(body.get("password") == "secret12", "password forwarded")
    assert_true(body.get("full_name") == "اختبار توجيه", "full_name forwarded")

    assert_true(r.status_code == 200, f"local register returns 200 (got {r.status_code})")
    data = r.get_json()
    assert_true(data.get("ok") is True, "response relays cloud ok=True")

    # The cloud response should NOT be re-registered in the LOCAL DB.
    local_user = db.get_user_by_username("tower_test")
    assert_true(local_user is None, "registration NOT duplicated in local DB (only cloud)")

    print("\n[PASSED] HYBRID REGISTER FORWARDING WORKS")

if __name__ == "__main__":
    try:
        main()
    finally:
        try:
            os.remove(_tmp_db.name)
        except OSError:
            pass