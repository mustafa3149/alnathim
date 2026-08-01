"""Smoke test for the API Gateway (Phase 8) — stdlib only.

Boots a real uvicorn server in a subprocess on an ephemeral port and
proves the endpoints work end-to-end, including auth (login + 401),
validation errors, clean JSON error bodies, and CORS preflight.

Safety: the subprocess uses ADMIN_PASSWORD=1234, MIKROTIK_HOST="" and an
isolated temp BILLING_DB_PATH so this test can NEVER touch a real router
or the real billing database.

Run:  py api_gateway\\test_api.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = None
PROC = None
CHECK = []
TOKEN = None

# Force UTF-8 output so Arabic error messages print correctly on Windows.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def free_port():
    """Return a currently-free TCP port on 127.0.0.1."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def start_server():
    """Launch uvicorn in a subprocess; return (proc, port, log_path)."""
    global PORT
    PORT = free_port()
    log_path = os.path.join(tempfile.gettempdir(), "api_gateway_test_%d.log" % os.getpid())

    env = dict(os.environ)
    # Force no-router + isolated DB + known admin password so the auth flow
    # runs deterministically and no real user/database is touched.
    env["MIKROTIK_HOST"] = ""
    env["MIKROTIK_USER"] = ""
    env["MIKROTIK_PASSWORD"] = ""
    env["BILLING_DB_PATH"] = os.path.join(tempfile.gettempdir(), "api_gateway_test_%d.db" % os.getpid())
    env["ADMIN_PASSWORD"] = "1234"
    env["PYTHONIOENCODING"] = "utf-8"

    logf = open(log_path, "wb")
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "api_gateway.main:app",
            "--host", "127.0.0.1",
            "--port", str(PORT),
            "--log-level", "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    logf.close()
    return proc, PORT, log_path


def http_request(method, path, payload=None, timeout=10, token=None):
    """Return (status_code, parsed_json) for a request to the test server.

    Defaults to the global TOKEN so the token set after login is used.
    """
    url = "http://127.0.0.1:%d%s" % (PORT, path)
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    auth = TOKEN if token is None else token
    if auth:
        headers["Authorization"] = "Bearer " + auth
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            parsed = json.loads(body) if body else None
            return resp.status, parsed
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = None
        return e.code, parsed


def wait_ready(timeout=40):
    """Poll until the server responds (200 or 401 proves it is up)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if PROC.poll() is not None:
            return False
        try:
            code, _ = http_request("GET", "/api/users", timeout=2)
            if code in (200, 401):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def check(name, cond, extra=""):
    """Record and print a named assertion."""
    CHECK.append((name, bool(cond)))
    if cond:
        print("  [PASS] %s" % name)
    else:
        print("  [FAIL] %s %s" % (name, extra))


def main():
    global PROC, TOKEN
    print("Starting uvicorn subprocess ...")
    PROC, port, log_path = start_server()

    try:
        if not wait_ready():
            print("Server failed to start. Log tail:")
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                print(f.read()[-4000:])
            return 1

        print("Server ready on port %d\n" % port)

        print("POST /api/login (auth) ...")
        code, data = http_request("POST", "/api/login", {"password": "1234"})
        check("login returns 200", code == 200, "(got %s)" % code)
        TOKEN = data.get("token") if isinstance(data, dict) else None
        check("login returns a token", bool(TOKEN), "(body=%s)" % (data or {}))
        check("login rejects wrong password",
              http_request("POST", "/api/login", {"password": "wrong"})[0] == 401)

        print("GET /api/users without token -> 401 ...")
        # token="" (empty) disables auth entirely; token=None uses the global.
        code, data = http_request("GET", "/api/users", token="")
        check("returns 401", code == 401, "(got %s)" % code)

        print("GET /api/users (authenticated) ...")
        code, data = http_request("GET", "/api/users", timeout=15)
        check("returns 200", code == 200, "(got %s)" % code)
        check("returns a JSON array", isinstance(data, list))
        if isinstance(data, list) and data:
            required = ("id", "name", "mikrotik_username", "status", "balance",
                        "expiry_date", "is_online", "ip_address")
            missing = [k for k in required if k not in data[0]]
            check("user objects have all required keys", not missing, "(missing=%s)" % missing)
            check("all offline without a router", all(not u["is_online"] for u in data))

        print("GET /api/signal/127.0.0.1 ...")
        code, data = http_request("GET", "/api/signal/127.0.0.1", timeout=30)
        check("returns 200", code == 200, "(got %s)" % code)
        check("returns a signal dict with status",
              isinstance(data, dict) and "status" in data, "(body=%s)" % data)

        print("POST /api/pay (invalid payload -> validation) ...")
        code, _ = http_request("POST", "/api/pay",
                               {"mikrotik_username": "x", "amount": 100, "days_to_add": 0})
        check("returns 422 on invalid input", code == 422, "(got %s)" % code)

        print("POST /api/pay (unknown user -> 404) ...")
        code, data = http_request("POST", "/api/pay",
                                  {"mikrotik_username": "no-such-user-xyz", "amount": 100, "days_to_add": 30})
        check("returns 404", code == 404, "(got %s)" % code)
        check("clean JSON error body",
              isinstance(data, dict) and isinstance(data.get("detail"), dict)
              and data["detail"].get("error") == "subscriber_not_found",
              "(body=%s)" % (data or {}))

        print("POST /api/sync (no router) ...")
        code, data = http_request("POST", "/api/sync", timeout=15)
        clean502 = isinstance(data, dict) and isinstance(data.get("detail"), dict) \
            and data["detail"].get("error") == "mikrotik_unreachable"
        summary_ok = isinstance(data, dict) and all(
            k in data for k in ("checked", "expired", "active", "disabled", "enabled", "missing", "errors")
        )
        check("returns 200 summary or clean 502", (code == 200 and summary_ok) or (code == 502 and clean502),
              "(got %s / %s)" % (code, data))

        print("OPTIONS preflight (CORS) ...")
        url = "http://127.0.0.1:%d/api/users" % PORT
        req = urllib.request.Request(url, method="OPTIONS")
        req.add_header("Origin", "http://localhost:3000")
        req.add_header("Access-Control-Request-Method", "GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                cors = resp.headers.get("Access-Control-Allow-Origin", "")
                check("preflight succeeds with CORS header", cors != "", "(allow-origin=%s)" % cors)
        except urllib.error.HTTPError as e:
            cors = e.headers.get("Access-Control-Allow-Origin", "") if e.headers else ""
            check("preflight succeeds with CORS header", cors != "", "(allow-origin=%s)" % cors)

        print("GET /api/nonexistent (unknown route) ...")
        code, _ = http_request("GET", "/api/nonexistent")
        check("returns 404", code == 404, "(got %s)" % code)

        print("")
        failed = [n for n, ok in CHECK if not ok]
        if failed:
            print("FAILED checks: %s" % ", ".join(failed))
            return 1
        print("ALL API GATEWAY SMOKE TESTS PASSED")
        return 0
    finally:
        if PROC is not None:
            PROC.terminate()
            try:
                PROC.wait(timeout=5)
            except subprocess.TimeoutExpired:
                PROC.kill()
                PROC.wait(timeout=5)
        try:
            os.remove(log_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())