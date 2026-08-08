"""Authentication & Authorization — Al-Nathim ISP Management.

Session-based login against the `users` table.
Roles: admin (full access + user management) / agent (normal access).
Phase 14.4: adds status/expiry checks on login, role-based landing
(admin → /admin, agent → /+), and the full admin-center API.
"""
from functools import wraps
from time import time
from datetime import datetime
from collections import defaultdict, deque
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session

import database as db
from config import (
    ALLOW_OPEN_REGISTRATION,
    SESSION_LIFETIME_MINUTES,
    MAX_FAILED_LOGINS,
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW_MINUTES,
    IS_RENDER,
    RELAY_URL,
    ACTIVATION_KEY_SECRET,
)

auth_bp = Blueprint("auth", __name__)

# ── Brute-force rate limiting (in-memory, per IP) ──────────
# Sliding window: <ip> -> deque of attempt timestamps.
_rate_attempts = defaultdict(deque)
_RATE_WINDOW_SECONDS = LOGIN_RATE_LIMIT_WINDOW_MINUTES * 60


def _rate_limited():
    """Return True when the caller's IP has exceeded the login attempt budget.

    Sliding-window check: prunes timestamps older than the window, then
    compares the remaining count to the configured limit.
    """
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    now = time()
    window = _rate_attempts[ip]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= LOGIN_RATE_LIMIT_ATTEMPTS:
        return True
    window.append(now)
    return False


def _clear_rate_limit():
    """Reset the rate-limit window for the caller's IP after a success."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    _rate_attempts[ip].clear()


# ── Machine-to-machine rate limiting (desktop launcher) ─────
# The tower EXE polls /api/remote/login and /api/auth/check-status while a
# registration is pending. Those are server-to-server calls, NOT human
# logins: the strict /login limiter would lock the tower out after a few
# polls, which made the launcher appear stuck on "بانتظار موافقة المدير"
# even after the admin approved the account. Use a generous budget here.
_MACHINE_RATE_ATTEMPTS = 120
_MACHINE_RATE_WINDOW_SECONDS = 5 * 60
_machine_rate_attempts = defaultdict(deque)


def _machine_rate_limited():
    """Return True when a server-to-server caller exceeded its poll budget."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    now = time()
    window = _machine_rate_attempts[ip]
    while window and now - window[0] > _MACHINE_RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _MACHINE_RATE_ATTEMPTS:
        return True
    window.append(now)
    return False


def _clear_machine_rate_limit():
    """Reset the machine poll budget for the caller's IP after a success."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    _machine_rate_attempts[ip].clear()


def _mirror_cloud_user(username, password, cloud_user):
    """Create or refresh the local user row to mirror a cloud-approved account.

    The cloud is the single source of truth for account state. This helper
    writes the password hash, role, status and access expiry into the local
    row so the launcher keeps working offline between polls and the local
    status/expiry gates always match what the admin set on the website.

    Args:
        username: the login name.
        password: the plaintext password the user typed (cloud verified it).
        cloud_user: the user dict returned by /api/remote/login.

    Returns:
        the refreshed local user row, or None.
    """
    local = db.get_user_by_username(username)
    if local is None:
        local_id = db.create_user(
            username, password,
            role=cloud_user.get("role", "agent"),
            full_name=cloud_user.get("full_name", ""),
            phone=cloud_user.get("phone", ""),
        )
        local = db.get_user_by_id(local_id) or db.get_user_by_username(username)
    if local is not None:
        db.update_user(
            local["id"],
            full_name=cloud_user.get("full_name", ""),
            phone=cloud_user.get("phone", ""),
            role=cloud_user.get("role", "agent"),
            status=cloud_user.get("status", "active"),
            password=password,
        )
        expires = cloud_user.get("access_expires") or None
        db.set_user_access_expiry(local["id"], expires)
        local = db.get_user_by_username(username)
    return local


# ── Helpers ─────────────────────────────────────────────────

def current_user():
    """Return the logged-in user row, or None."""
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.get_user_by_id(user_id)


def current_user_id():
    """Return the logged-in user id, or None."""
    return session.get("user_id")


def current_role():
    """Return the session role, or None."""
    return session.get("user_role")


# ── Decorators ──────────────────────────────────────────────

def login_required(fn):
    """Require an authenticated session."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """Require an authenticated admin session."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login"))
        if session.get("user_role") != "admin":
            return jsonify({"ok": False, "error": "هذه العملية تتطلب صلاحيات المدير"}), 403
        return fn(*args, **kwargs)
    return wrapper


# ── Routes: Login / Logout / Register ───────────────────────

# Dummy hash for timing-safe verification when the username doesn't exist —
# ensures an unknown-username login takes the same time as a wrong password.
_DUMMY_HASH = generate_password_hash("timing-equalizer-not-a-real-password")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Render the login page or authenticate a user."""
    if session.get("user_id"):
        return redirect(url_for("dashboard") if session.get("user_role") != "admin" else "/admin")

    if request.method == "GET":
        return render_template("login.html")

    # Phase 14.5: brute-force rate limit (per IP)
    if _rate_limited():
        return jsonify({"ok": False, "error": "محاولات كثيرة — حاول بعد قليل"}), 429

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "أدخل اسم المستخدم وكلمة المرور"}), 400

    user = db.get_user_by_username(username)

    # Timing-safe: always run a real hash check (dummy when user is unknown).
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        password_ok = False
    else:
        password_ok = db.verify_password(user, password)

    # ── Hybrid: local EXE accepts cloud accounts too (same web login) ──
    # When this app is NOT running on Render (desktop EXE at the tower) we ask
    # the cloud to validate the credentials and mirror the account locally.
    #
    # Phase 14.7 FIX: this re-check ALSO runs when the local user exists but is
    # `pending`/`suspended`. Before this fix, a registration that was approved
    # only in the cloud DB stayed "بانتظار موافقة المدير" forever on the EXE
    # because the local pending row blocked the cloud sync.
    _local_status = str((user["status"] if user is not None else "") or "")
    _needs_cloud_check = (
        not IS_RENDER
        and RELAY_URL
        and (
            user is None
            or not password_ok
            or _local_status in ("pending", "suspended")
        )
    )
    # The cloud is the source of truth for account state. When it answers
    # with an explicit state (pending/suspended/expired) we surface that
    # instead of a generic 'invalid credentials' so the launcher can show
    # the right screen (waiting / blocked / expired).
    cloud_state = None
    cloud_unreachable = False
    if _needs_cloud_check:
        try:
            import json as _json
            import urllib.request as _ur
            _req = _ur.Request(
                RELAY_URL.rstrip("/") + "/api/remote/login",
                data=_json.dumps({"username": username, "password": password}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            import urllib.error as _uerr
            try:
                with _ur.urlopen(_req, timeout=90) as _resp:
                    _res = _json.loads(_resp.read().decode("utf-8", "replace"))
            except _uerr.HTTPError as _e:
                # Non-2xx still carries a JSON verdict (pending/suspended/
                # expired/busy) that the launcher must surface.
                try:
                    _res = _json.loads(_e.read().decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    _res = {}
            if isinstance(_res, dict):
                cloud_state = _res.get("state")
                if cloud_state == "busy":
                    cloud_state = None
                    cloud_unreachable = True
            if _res.get("ok") and _res.get("user"):
                # Approved on the cloud → mirror password/status/expiry locally
                # so the launcher keeps working offline between polls.
                user = _mirror_cloud_user(username, password, _res["user"])
                password_ok = bool(user) and db.verify_password(user, password)
                if password_ok:
                    db.reset_failed_logins(user["id"])
                    _clear_rate_limit()
        except Exception as e:  # noqa: BLE001 — cloud unreachable
            cloud_unreachable = True
            import logging
            logging.getLogger(__name__).warning("[HybridLogin] cloud check failed: %s", e)

    # Cloud is authoritative for pending/suspended/expired — never count
    # those as failed logins locally (the password was already verified
    # on the cloud).
    if cloud_state == "suspended":
        return jsonify({"ok": False, "state": "suspended", "error": "الحساب موقوف — راجع المدير"}), 403
    if cloud_state == "expired":
        return jsonify({"ok": False, "state": "expired", "error": "انتهت صلاحية الدخول — راجع المدير"}), 403
    if cloud_state == "pending":
        return jsonify({"ok": False, "state": "pending", "error": "بانتظار موافقة المدير"}), 403

    # Cloud unreachable while the local copy is still pending/suspended →
    # tell the truth (offline), not a misleading 'pending' forever.
    if cloud_unreachable and _local_status in ("pending", "suspended"):
        return jsonify({"ok": False, "state": "offline", "error": "تعذر الاتصال بالخادم — تأكد من الإنترنت وأعد المحاولة"}), 503

    # Phase 14.5: failed-login lockout
    if not password_ok:
        local_user = db.get_user_by_username(username)
        if local_user is not None:
            db.increment_failed_logins(local_user["id"])
            if db.count_failed_logins(local_user["id"]) >= MAX_FAILED_LOGINS:
                db.lock_user(local_user["id"])
                db.log_action(
                    user_id=local_user["id"],
                    username=local_user["full_name"] or local_user["username"],
                    action="قفل الحساب",
                    target_type="user",
                    target_id=local_user["id"],
                    details=f"تم قفل الحساب تلقائياً بعد {MAX_FAILED_LOGINS} محاولات فاشلة",
                )
                return jsonify({"ok": False, "state": "locked", "error": "تم قفل الحساب بسبب محاولات كثيرة — راجع المدير"}), 403
        return jsonify({"ok": False, "state": "invalid", "error": "بيانات الدخول غير صحيحة"}), 400

    # Safety: after successful verification the user must exist locally.
    if user is None:
        return jsonify({"ok": False, "state": "invalid", "error": "بيانات الدخول غير صحيحة"}), 400

    # Reset the failed-login counter on success.
    db.reset_failed_logins(user["id"])
    _clear_rate_limit()

    # Phase 14.4: status / expiry gate (local copy is authoritative here).
    _status = str(user["status"] or "active")
    if _status == "suspended":
        return jsonify({"ok": False, "state": "suspended", "error": "الحساب موقوف — راجع المدير"}), 403
    if _status == "pending":
        return jsonify({"ok": False, "state": "pending", "error": "بانتظار موافقة المدير"}), 403
    if not db.is_user_active(user):
        return jsonify({"ok": False, "state": "expired", "error": "انتهت صلاحية الدخول — راجع المدير"}), 403

    session.permanent = True
    session["user_id"] = user["id"]
    session["user_role"] = user["role"]
    session["user_name"] = user["full_name"] or user["username"]
    session["account_id"] = user["account_id"]
    db.log_action(
        user_id=user["id"],
        username=user["full_name"] or user["username"],
        action="تسجيل دخول",
        details=f"تم تسجيل دخول {user['full_name'] or user['username']}",
    )
    return jsonify(
        {
            "ok": True,
            "state": "approved",
            "message": "تم تسجيل الدخول بنجاح",
            "redirect": "/admin" if user["role"] == "admin" else "/",
        }
    )


@auth_bp.route("/api/remote/login", methods=["POST"])
def api_remote_login():
    """Cloud-side credential check used by the local desktop EXE.

    The tower EXE keeps its own local SQLite, but users are approved on the
    cloud. When a user tries to log in on the EXE, it forwards the username +
    password here; on success the EXE mirrors the account locally.

    This endpoint is intentionally CSRF-exempt (server-to-server call). It
    uses the generous machine budget, NOT the human /login limiter — otherwise
    a launcher that polls while a registration is pending would be locked out
    and stay stuck on "بانتظار موافقة المدير" after approval.
    """
    if _machine_rate_limited():
        return jsonify({"ok": False, "state": "busy", "error": "محاولات كثيرة — حاول بعد قليل"}), 429

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "state": "invalid", "error": "أدخل اسم المستخدم وكلمة المرور"}), 400

    user = db.get_user_by_username(username)

    # Timing-safe path for an unknown user.
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        return jsonify({"ok": False, "state": "invalid", "error": "بيانات الدخول غير صحيحة"}), 400

    if not db.verify_password(user, password):
        db.increment_failed_logins(user["id"])
        return jsonify({"ok": False, "state": "invalid", "error": "بيانات الدخول غير صحيحة"}), 400

    # Only active accounts can log in — but report the exact state so the
    # launcher can show the right screen (waiting vs. blocked vs. expired).
    _status = str(user["status"] or "active")
    if _status == "suspended":
        return jsonify({"ok": False, "state": "suspended", "error": "الحساب موقوف — راجع المدير"}), 403
    if _status == "pending":
        return jsonify({"ok": False, "state": "pending", "error": "بانتظار موافقة المدير"}), 403
    if not db.is_user_active(user):
        return jsonify({"ok": False, "state": "expired", "error": "انتهت صلاحية الدخول — راجع المدير"}), 403

    db.reset_failed_logins(user["id"])
    _clear_machine_rate_limit()
    return jsonify({
        "ok": True,
        "state": "approved",
        "user": {
            "username": user["username"],
            "full_name": user["full_name"] or "",
            "phone": user["phone"] or "",
            "role": user["role"],
            "status": user["status"] or "active",
            "access_expires": user["access_expires"] or None,
        },
    })


def _sign_activation(user_id, hwid, expires_at=None):
    """Generate a signed activation key (HMAC-SHA256) for a desktop PC.

    Args:
        user_id: target user account id.
        hwid: desktop hardware id.
        expires_at: optional expiry timestamp string; None = permanent.

    Returns:
        str: base32-encoded key payload "user_id.hwid.exp.signature".
    """
    import base64
    import hashlib
    import hmac

    payload = f"{user_id}.{hwid}.{expires_at or '0'}"
    sig = hmac.new(
        ACTIVATION_KEY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig_b32 = base64.b32encode(sig).decode("utf-8").rstrip("=")[:24]
    return f"{payload}.{sig_b32}"


def _parse_activation_key(key):
    """Validate a signed activation key and return its payload dict or None.

    Args:
        key: the activation key string generated by `_sign_activation`.

    Returns:
        dict with {user_id, hwid, expires_at} when valid, None when invalid.
    """
    import base64
    import hashlib
    import hmac

    key = (key or "").strip()
    parts = key.split(".")
    if len(parts) != 4:
        return None
    user_id_raw, hwid, expires_raw, sig_b32 = parts
    payload = f"{user_id_raw}.{hwid}.{expires_raw}"
    expected = hmac.new(
        ACTIVATION_KEY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_b32 = base64.b32encode(expected).decode("utf-8").rstrip("=")[:24]
    if sig_b32 != expected_b32:
        return None
    try:
        user_id = int(user_id_raw)
    except (TypeError, ValueError):
        return None
    if not hwid:
        return None
    return {
        "user_id": user_id,
        "hwid": hwid,
        "expires_at": expires_raw if expires_raw != "0" else None,
    }


@auth_bp.route("/api/auth/check-status", methods=["POST"])
def api_auth_check_status():
    """Desktop launcher polling endpoint for approval status.

    Body: {"username": "...", "password": "..."}
    Returns:
        {"ok": true, "state": "approved", "user_id": N, "token": "...",
         "redirect": "/..."} when the account is active, else
        {"ok": false, "state": "pending"|"suspended"|"expired"|
         "invalid"|"offline"} so the launcher can show the right screen.
    """
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "state": "invalid", "error": "بيانات ناقصة"}), 400

    user = db.get_user_by_username(username)
    password_ok = bool(user) and db.verify_password(user, password)
    local_approved = password_ok and db.is_user_active(user)

    # Cloud relay: in hybrid mode the cloud is always consulted so the poll
    # reflects the admin's latest decision (approve/suspend/extend). If the
    # cloud is unreachable we fall back to the local copy.
    cloud_state = None
    cloud_unreachable = False
    if not IS_RENDER and RELAY_URL:
        try:
            import json as _json
            import urllib.request as _ur
            _req = _ur.Request(
                RELAY_URL.rstrip("/") + "/api/remote/login",
                data=_json.dumps({"username": username, "password": password}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            import urllib.error as _uerr
            try:
                with _ur.urlopen(_req, timeout=90) as _resp:
                    _res = _json.loads(_resp.read().decode("utf-8", "replace"))
            except _uerr.HTTPError as _e:
                # Non-2xx still carries a JSON verdict (pending/suspended/
                # expired/busy) that the launcher must surface.
                try:
                    _res = _json.loads(_e.read().decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    _res = {}
            if isinstance(_res, dict):
                cloud_state = _res.get("state")
                if cloud_state == "busy":
                    cloud_state = None
                    cloud_unreachable = True
            if _res.get("ok") and _res.get("user"):
                user = _mirror_cloud_user(username, password, _res["user"])
                password_ok = bool(user) and db.verify_password(user, password)
        except Exception as e:  # noqa: BLE001 — cloud unreachable
            cloud_unreachable = True
            import logging
            logging.getLogger(__name__).warning("[CheckStatus] cloud check failed: %s", e)

    if user is None:
        user = db.get_user_by_username(username)

    # Local copy wins when it is already approved (offline tolerance).
    if local_approved or (password_ok and user is not None and db.is_user_active(user)):
        uid = user["id"]
        # Rotate a fresh desktop token so the launcher stores something usable.
        token = _sign_activation(uid, "launcher", None) if uid else ""
        session.permanent = True
        session["user_id"] = uid
        session["user_role"] = user["role"]
        session["user_name"] = user["full_name"] or user["username"]
        return jsonify({
            "ok": True,
            "state": "approved",
            "user_id": uid,
            "token": token,
            "redirect": "/admin" if user["role"] == "admin" else "/",
        })

    # Explicit cloud verdicts beat any local guess.
    if cloud_state in ("pending", "suspended", "expired", "invalid"):
        return jsonify({"ok": False, "state": cloud_state})

    if cloud_unreachable:
        return jsonify({"ok": False, "state": "offline", "error": "تعذر الاتصال بالخادم"})

    # No cloud verdict → derive from the local row.
    if user is not None:
        _status = str(user["status"] or "active")
        if _status == "suspended":
            return jsonify({"ok": False, "state": "suspended"})
        if _status == "pending":
            return jsonify({"ok": False, "state": "pending"})
        if not db.is_user_active(user):
            return jsonify({"ok": False, "state": "expired"})
    return jsonify({"ok": False, "state": "pending"})


@auth_bp.route("/api/device/hwid", methods=["GET"])
def api_device_hwid():
    """Return this machine's stable hardware id (works on Windows desktop).

    Uses the MAC address of the first non-virtual network adapter. Falls back
    to a persisted random id in the app-data dir so the same PC keeps the
    same HWID across reboots/reinstalls.
    """
    import getpass
    import hashlib
    import os as _os
    import uuid

    hwid = ""
    try:
        mac = uuid.getnode()
        if (mac >> 40) % 2 == 0:  # unicast, locally-administered check
            hwid = hashlib.sha256(f"alnathim-{mac}".encode("utf-8")).hexdigest()[:24].upper()
    except Exception:  # noqa: BLE001
        pass

    if not hwid:
        # Persist a random device id in AppData so it stays stable.
        _file = _os.path.join(_os.environ.get("APPDATA", ""), "AlNathim", "device.id")
        try:
            if _os.path.exists(_file):
                with open(_file, "r", encoding="utf-8") as _f:
                    hwid = _f.read().strip()
            if not hwid:
                hwid = hashlib.sha256(f"{getpass.getuser()}-{uuid.uuid4()}".encode()).hexdigest()[:24].upper()
                _os.makedirs(_os.path.dirname(_file), exist_ok=True)
                with open(_file, "w", encoding="utf-8") as _f:
                    _f.write(hwid)
        except Exception:  # noqa: BLE001
            hwid = hashlib.sha256(f"fallback-{getpass.getuser()}".encode()).hexdigest()[:24].upper()

    return jsonify({"ok": True, "hwid": hwid})


@auth_bp.route("/api/device/activate", methods=["POST"])
def api_device_activate():
    """Validate a desktop activation key and unlock the launcher.

    Body: {"activation_key": "...", "hwid": "..."}
    On success the user record's HWID is bound and the endpoint returns the
    embedded user_id + a token for MikroTik sync.
    """
    data = request.get_json() or {}
    key = data.get("activation_key", "").strip()
    hwid = (data.get("hwid") or "").strip()

    if not key or not hwid:
        return jsonify({"ok": False, "error": "أدخل كود التفعيل ومعرّف الجهاز"}), 400

    payload = _parse_activation_key(key)
    if payload is None:
        return jsonify({"ok": False, "error": "كود التفعيل غير صالح"}), 400

    # The key is bound to a specific HWID — reject if it was issued for another.
    if payload["hwid"] and payload["hwid"] != hwid:
        return jsonify({"ok": False, "error": "كود التفعيل لا يخص هذا الجهاز"}), 403

    # Also verify against the stored activation record (key must exist).
    stored = db.get_device_activation_by_key(key)
    if stored is None or stored["user_id"] != payload["user_id"]:
        return jsonify({"ok": False, "error": "كود التفعيل غير مسجل"}), 403

    # Expiry check (permanent when expires_at is None).
    if stored["expires_at"] and str(stored["expires_at"]) < db.now_str():
        return jsonify({"ok": False, "error": "انتهت صلاحية كود التفعيل"}), 403

    target = db.get_user_by_id(payload["user_id"])
    if target is None:
        return jsonify({"ok": False, "error": "الحساب غير موجود"}), 404
    if not db.is_user_active(target):
        return jsonify({"ok": False, "error": "الحساب غير نشط"}), 403

    db.set_user_hwid(target["id"], hwid)
    token = _sign_activation(target["id"], hwid, None)
    db.log_action(
        user_id=target["id"],
        username=target["full_name"] or target["username"],
        action="تفعيل حاسبة",
        target_type="user",
        target_id=target["id"],
        details=f"تم تفعيل الحاسبة {hwid} للحساب {target['username']}",
    )
    return jsonify({
        "ok": True,
        "approved": True,
        "user_id": target["id"],
        "token": token,
    })


@auth_bp.route("/api/device/status", methods=["POST"])
def api_device_status():
    """Poll approval state for a bound machine.

    Body: {"hwid": "...", "activation_key": "..."} (key optional).
    Returns {"approved": true, "user_id": N} when a valid activation exists.
    """
    data = request.get_json() or {}
    hwid = (data.get("hwid") or "").strip()
    key = (data.get("activation_key") or "").strip()

    if not hwid:
        return jsonify({"ok": False, "error": "معرّف الجهاز مطلوب"}), 400

    # Prefer an explicit key first.
    if key:
        payload = _parse_activation_key(key)
        if payload and payload["hwid"] == hwid:
            stored = db.get_device_activation_by_key(key)
            if stored and (not stored["expires_at"] or str(stored["expires_at"]) >= db.now_str()):
                target = db.get_user_by_id(stored["user_id"])
                if target and db.is_user_active(target):
                    return jsonify({"approved": True, "user_id": target["id"]})

    # Fallback: find any activation row for this HWID that is still valid.
    for act in db.list_device_activations():
        if act["hwid"] == hwid:
            if act["expires_at"] and str(act["expires_at"]) < db.now_str():
                continue
            target = db.get_user_by_id(act["user_id"])
            if target and db.is_user_active(target):
                return jsonify({"approved": True, "user_id": target["id"], "activation_key": act["activation_key"]})

    return jsonify({"approved": False})


@auth_bp.route("/api/admin/activate-pc", methods=["POST"])
@admin_required
def api_admin_activate_pc():
    """Admin tool: generate a signed activation key for a desktop PC.

    Body: {"hwid": "...", "username": "...", "duration": "day"|"30d"|"forever"}
    """
    data = request.get_json() or {}
    hwid = (data.get("hwid") or "").strip()
    username = (data.get("username") or "").strip()
    duration = (data.get("duration") or "forever").strip()
    custom_expires = (data.get("expires_at") or "").strip()

    if not hwid or not username:
        return jsonify({"ok": False, "error": "أدخل معرّف الجهاز واسم الحساب"}), 400

    target = db.get_user_by_username(username)
    if target is None:
        return jsonify({"ok": False, "error": "الحساب غير موجود"}), 404

    # Compute the activation expiry.
    from datetime import timedelta
    expires_at = None  # permanent
    if duration == "custom" and custom_expires:
        # Date picked from the calendar — accept "YYYY-MM-DDTHH:MM" (datetime-local)
        # or "YYYY-MM-DD HH:MM" / "YYYY-MM-DD".
        try:
            dt = datetime.strptime(custom_expires, "%Y-%m-%dT%H:%M")
        except ValueError:
            try:
                dt = datetime.strptime(custom_expires, "%Y-%m-%d %H:%M")
            except ValueError:
                try:
                    dt = datetime.strptime(custom_expires, "%Y-%m-%d")
                    dt = dt.replace(hour=23, minute=59)
                except ValueError:
                    return jsonify({"ok": False, "error": "تاريخ غير صالح"}), 400
        expires_at = dt.strftime(db.DATETIME_DB)
    elif duration == "day":
        expires_at = (datetime.now() + timedelta(days=1)).strftime(db.DATETIME_DB)
    elif duration == "30d":
        expires_at = (datetime.now() + timedelta(days=30)).strftime(db.DATETIME_DB)
    elif duration != "forever":
        return jsonify({"ok": False, "error": "مدة غير صالحة"}), 400

    key = _sign_activation(target["id"], hwid, expires_at)
    db.save_device_activation(target["id"], hwid, key, expires_at)

    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="تفعيل حاسبة",
        target_type="user",
        target_id=target["id"],
        details=f"تم إنشاء كود تفعيل للحاسبة {hwid} للمستخدم {username}",
    )
    # Return readable expiry text for the admin UI.
    if duration == "day":
        expiry_label = "يوم واحد"
    elif duration == "30d":
        expiry_label = "٣٠ يوم"
    elif duration == "custom" and expires_at:
        expiry_label = "حتى " + expires_at
    else:
        expiry_label = "دائم"
    return jsonify({
        "ok": True,
        "activation_key": key,
        "expires_at": expires_at,
        "expiry_label": expiry_label,
        "username": username,
        "hwid": hwid,
        "message": "تم إنشاء كود التفعيل",
    })


@auth_bp.route("/api/admin/activate-pc/list", methods=["GET"])
@admin_required
def api_admin_activate_pc_list():
    """Return all desktop activation records for the admin center."""
    acts = db.list_device_activations()
    return jsonify({
        "ok": True,
        "activations": [
            {
                "id": a["id"],
                "username": a["username"],
                "full_name": a["full_name"] or "",
                "hwid": a["hwid"],
                "activation_key": a["activation_key"],
                "expires_at": a["expires_at"],
                "created_at": a["created_at"],
            }
            for a in acts
        ],
    })


@auth_bp.route("/api/admin/activate-pc/<int:activation_id>/revoke", methods=["POST"])
@admin_required
def api_admin_activate_pc_revoke(activation_id):
    """Revoke (delete) a desktop activation record."""
    if not db.revoke_device_activation(activation_id):
        return jsonify({"ok": False, "error": "رمز التفعيل غير موجود"}), 404
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="إلغاء تفعيل حاسبة",
        details=f"تم إلغاء تفعيل الحاسبة #{activation_id}",
    )
    return jsonify({"ok": True, "message": "تم إلغاء التفعيل"})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Clear the session."""
    uid = session.get("user_id")
    uname = session.get("user_name") or ""
    if uid:
        db.log_action(user_id=uid, username=uname, action="تسجيل خروج",
                      details=f"تم تسجيل خروج {uname}")
    session.pop("user_id", None)
    session.pop("user_role", None)
    session.pop("user_name", None)
    return jsonify({"ok": True, "message": "تم تسجيل الخروج"})


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Public registration: with a valid invite code → active instantly;
    without one → pending until an admin approves in the admin center.

    When ALLOW_OPEN_REGISTRATION is off, registration is disabled entirely
    and only an admin can create accounts in the Admin Center.
    """
    if not ALLOW_OPEN_REGISTRATION:
        return jsonify({"ok": False, "error": "التسجيل الذاتي معطّل حالياً — راجع المدير"}), 403

    if request.method == "GET":
        return render_template("register.html")

    # Phase 14.5: brute-force rate limit (per IP) on registration
    if _rate_limited():
        return jsonify({"ok": False, "error": "محاولات كثيرة — حاول بعد قليل"}), 429

    data = request.get_json() or {}
    full_name = data.get("full_name", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")
    phone = data.get("phone", "").strip()
    invite_code = data.get("invite_code", "").strip()

    # ── Hybrid: local desktop EXE forwards the registration to the cloud ──
    # When this app is NOT running on Render (local EXE / laptop), the pending
    # registration must go to the cloud DB so the admin sees it on the website.
    # The cloud (Render) itself processes registrations locally (no recursion).
    if not IS_RENDER and RELAY_URL:
        try:
            import json as _json
            import urllib.request as _ur
            _url = RELAY_URL.rstrip("/") + "/register"
            _req = _ur.Request(
                _url,
                data=_json.dumps({
                    "full_name": full_name, "username": username,
                    "password": password, "phone": phone,
                    "invite_code": invite_code,
                }).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _ur.urlopen(_req, timeout=90) as _resp:
                _res = _json.loads(_resp.read().decode("utf-8", "replace"))
            if _res.get("ok"):
                return jsonify(_res)
            return jsonify(_res), 400
        except Exception as e:  # noqa: BLE001 — cloud unreachable → local fallback
            import logging
            logging.getLogger(__name__).warning("[HybridRegister] cloud forward failed: %s", e)

    if not full_name or not username or not password:
        return jsonify({"ok": False, "error": "الاسم واسم المستخدم وكلمة المرور مطلوبة"}), 400
    if len(username) < 3:
        return jsonify({"ok": False, "error": "اسم المستخدم يجب أن يكون ٣ أحرف على الأقل"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "كلمة المرور يجب أن تكون ٦ أحرف على الأقل"}), 400

    try:
        result = db.create_registration(
            full_name=full_name,
            username=username,
            password=password,
            phone=phone,
            invite_code=invite_code,
        )
    except Exception:
        return jsonify({"ok": False, "error": "اسم المستخدم مستخدم مسبقاً"}), 400

    if result["status"] == "active":
        return jsonify({"ok": True, "message": "تم إنشاء الحساب بنجاح — يمكنك تسجيل الدخول"})
    # When running as a desktop EXE WITHOUT a working cloud relay, the pending
    # request exists only in this device's local DB — it will NOT appear in the
    # cloud admin's queue. Say so instead of promising it reached the website.
    if not IS_RENDER:
        return jsonify({
            "ok": True,
            "message": "تم حفظ طلب التسجيل على هذا الجهاز — بانتظار الموافقة من مدير النظام",
        })


    return jsonify(
        {"ok": True, "message": "تم إرسال طلب التسجيل — بانتظار موافقة المدير"}
    )


# ── API: User Management (admin only) ───────────────────────

@auth_bp.route("/api/users/list")
@admin_required
def api_users_list():
    """Return all users with admin-center fields."""
    users = db.list_users()
    return jsonify({
        "ok": True,
        "users": [
            {
                "id": u["id"],
                "username": u["username"],
                "role": u["role"],
                "full_name": u["full_name"],
                "phone": u["phone"],
                "status": u["status"],
                "access_expires": u["access_expires"],
                "invite_code": u["invite_code"],
                "invite_uses": u["invite_uses"],
                "invite_max_uses": u["invite_max_uses"],
                "failed_logins": u["failed_logins"],
            }
            for u in users
        ],
    })


@auth_bp.route("/api/users/add", methods=["POST"])
@admin_required
def api_users_add():
    """Create a new user."""
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")
    role = data.get("role", "agent")
    full_name = data.get("full_name", "").strip()
    phone = data.get("phone", "").strip()

    if not username or not password:
        return jsonify({"ok": False, "error": "اسم المستخدم وكلمة المرور مطلوبان"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "كلمة المرور يجب أن تكون ٦ أحرف على الأقل"}), 400
    if role not in ("admin", "agent"):
        role = "agent"

    try:
        user_id = db.create_user(username, password, role, full_name, phone)
    except Exception:
        return jsonify({"ok": False, "error": "اسم المستخدم مستخدم مسبقاً"}), 400

    return jsonify({"ok": True, "user_id": user_id, "message": "تم إنشاء المستخدم بنجاح"})


@auth_bp.route("/api/users/edit/<int:user_id>", methods=["POST"])
@admin_required
def api_users_edit(user_id):
    """Update a user's profile fields."""
    data = request.get_json() or {}
    fields = {
        "full_name": data.get("full_name"),
        "phone": data.get("phone"),
        "role": data.get("role") if data.get("role") in ("admin", "agent") else None,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if "role" in fields and user_id == session.get("user_id") and fields["role"] != "admin":
        return jsonify({"ok": False, "error": "لا يمكنك إزالة صلاحيات المدير عن نفسك"}), 400

    try:
        db.update_user(user_id, **fields)
    except Exception:
        return jsonify({"ok": False, "error": "تعذر تحديث المستخدم"}), 400

    if user_id == session.get("user_id") and "role" in fields:
        session["user_role"] = fields["role"]
    if user_id == session.get("user_id") and "full_name" in fields:
        session["user_name"] = fields["full_name"] or ""
    return jsonify({"ok": True, "message": "تم تحديث المستخدم"})


@auth_bp.route("/api/users/reset-password/<int:user_id>", methods=["POST"])
@admin_required
def api_users_reset_password(user_id):
    """Reset a user's password."""
    data = request.get_json() or {}
    new_password = data.get("new_password", "")
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "كلمة المرور يجب أن تكون ٦ أحرف على الأقل"}), 400

    db.update_user(user_id, password=new_password)
    return jsonify({"ok": True, "message": "تم تغيير كلمة المرور"})


@auth_bp.route("/api/users/delete/<int:user_id>", methods=["POST"])
@admin_required
def api_users_delete(user_id):
    """Delete a user (cannot delete yourself)."""
    if user_id == session.get("user_id"):
        return jsonify({"ok": False, "error": "لا يمكنك حذف حسابك الحالي"}), 400

    db.delete_user(user_id)
    return jsonify({"ok": True, "message": "تم حذف المستخدم"})


@auth_bp.route("/api/admin/users/reset-all", methods=["POST"])
@admin_required
def api_admin_reset_all_users():
    """Delete every non-admin user (agents) — server reset keeping the admin.

    Warning: permanently removes all agent accounts. Registered devices and
    all non-admin access are cleared. The admin (owner) account survives.
    """
    deleted = db.delete_all_agents()
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="إعادة تعيين المستخدمين",
        target_type="users",
        target_id=None,
        details=f"تم حذف {deleted} حساب غير مدير (وكلاء) — بقي حساب المدير",
    )
    return jsonify({"ok": True, "deleted": deleted, "message": "تم حذف جميع الوكلاء"})


@auth_bp.route("/api/change-password", methods=["POST"])
@login_required
def api_change_password():
    """Change the logged-in user's own password."""
    data = request.get_json() or {}
    current = data.get("current_password", "")
    new_password = data.get("new_password", "")
    user = current_user()

    if not user:
        return jsonify({"ok": False, "error": "الجلسة غير صالحة"}), 401
    if not current or not new_password:
        return jsonify({"ok": False, "error": "أدخل كلمة المرور الحالية والجديدة"}), 400
    if len(new_password) < 6:
        return jsonify({"ok": False, "error": "كلمة المرور الجديدة يجب أن تكون ٦ أحرف على الأقل"}), 400
    if not db.verify_password(user, current):
        return jsonify({"ok": False, "error": "كلمة المرور الحالية غير صحيحة"}), 400

    db.update_user(user["id"], password=new_password)
    return jsonify({"ok": True, "message": "تم تغيير كلمة المرور بنجاح"})


# ── Admin Center API (Phase 14.4) ───────────────────────────

@auth_bp.route("/api/admin/registrations")
@admin_required
def api_admin_registrations():
    """Return pending registration requests."""
    users = [u for u in db.list_users() if u["status"] == "pending"]
    return jsonify({
        "ok": True,
        "registrations": [
            {
                "id": u["id"],
                "full_name": u["full_name"],
                "username": u["username"],
                "phone": u["phone"],
                # created_at is added via schema migration; older DBs (or
                # legacy rows) may lack the column — read defensively.
                "created_at": (u["created_at"] if "created_at" in u.keys() else "") or "",
            }
            for u in users
        ],
    })


@auth_bp.route("/api/admin/registrations/<int:user_id>/approve", methods=["POST"])
@admin_required
def api_admin_approve(user_id):
    """Approve a pending registration."""
    if not db.approve_user(user_id):
        return jsonify({"ok": False, "error": "المستخدم غير موجود أو ليس بانتظار الموافقة"}), 400
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="موافقة على تسجيل",
        target_type="user",
        target_id=user_id,
        details=f"تمت الموافقة على طلب تسجيل المستخدم #{user_id}",
    )
    return jsonify({"ok": True, "message": "تمت الموافقة على الطلب"})


@auth_bp.route("/api/admin/registrations/<int:user_id>/reject", methods=["POST"])
@admin_required
def api_admin_reject(user_id):
    """Reject (delete) a pending registration."""
    if not db.reject_user(user_id):
        return jsonify({"ok": False, "error": "المستخدم غير موجود أو ليس بانتظار الموافقة"}), 400
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="رفض تسجيل",
        target_type="user",
        target_id=user_id,
        details=f"تم رفض طلب تسجيل المستخدم #{user_id}",
    )
    return jsonify({"ok": True, "message": "تم رفض الطلب"})


@auth_bp.route("/api/admin/users/<int:user_id>/suspend", methods=["POST"])
@admin_required
def api_admin_suspend(user_id):
    """Suspend an active user."""
    if user_id == session.get("user_id"):
        return jsonify({"ok": False, "error": "لا يمكنك إيقاف حسابك الحالي"}), 400
    db.set_user_status(user_id, "suspended")
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="إيقاف حساب",
        target_type="user",
        target_id=user_id,
        details=f"تم إيقاف حساب المستخدم #{user_id}",
    )
    return jsonify({"ok": True, "message": "تم إيقاف الحساب"})


@auth_bp.route("/api/admin/users/<int:user_id>/activate", methods=["POST"])
@admin_required
def api_admin_activate(user_id):
    """Reactivate a suspended (or expired) user."""
    db.set_user_status(user_id, "active")
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="تفعيل حساب",
        target_type="user",
        target_id=user_id,
        details=f"تم تفعيل حساب المستخدم #{user_id}",
    )
    return jsonify({"ok": True, "message": "تم تفعيل الحساب"})


@auth_bp.route("/api/admin/users/<int:user_id>/access", methods=["POST"])
@admin_required
def api_admin_grant_access(user_id):
    """Grant timed access for a user (days / hours / specific date)."""
    data = request.get_json() or {}
    expires = data.get("access_expires", "").strip()
    days = int(data.get("days") or 0)
    hours = int(data.get("hours") or 0)

    if expires:
        # Specific datetime string: "YYYY-MM-DD HH:MM" or "YYYY-MM-DD"
        try:
            dt = datetime.strptime(expires, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                dt = datetime.strptime(expires, "%Y-%m-%d")
                dt = dt.replace(hour=23, minute=59)
            except ValueError:
                return jsonify({"ok": False, "error": "تاريخ غير صالح"}), 400
        ok = db.set_user_access_expiry(user_id, dt)
    else:
        ok = db.grant_timed_access(user_id, duration_hours=hours, duration_days=days) or bool(days or hours)

    if not ok:
        return jsonify({"ok": False, "error": "لم يتم تحديد مدة صالحة"}), 400

    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="منح وصول",
        target_type="user",
        target_id=user_id,
        details=f"تم منح وصول مؤقت للمستخدم #{user_id}",
    )
    return jsonify({"ok": True, "message": "تم منح الوصول"})


@auth_bp.route("/api/admin/users/<int:user_id>/clear-access", methods=["POST"])
@admin_required
def api_admin_clear_access(user_id):
    """Remove a user's access expiry (unlimited)."""
    db.set_user_access_expiry(user_id, None)
    return jsonify({"ok": True, "message": "تمت إزالة قيد الوصول"})


@auth_bp.route("/api/admin/users/<int:user_id>/invite", methods=["POST"])
@admin_required
def api_admin_generate_invite(user_id):
    """Generate an invite/entry code for a user (max uses). Returns the code."""
    data = request.get_json() or {}
    max_uses = int(data.get("max_uses") or 1)
    code = db.generate_invite_code(user_id, max_uses=max_uses)
    db.log_action(
        user_id=session.get("user_id"),
        username=session.get("user_name") or "",
        action="إنشاء رمز دخول",
        target_type="user",
        target_id=user_id,
        details=f"تم إنشاء رمز دخول للمستخدم #{user_id}",
    )
    return jsonify({"ok": True, "code": code, "message": "تم إنشاء رمز الدخول"})


# ── Admin Center page ───────────────────────────────────────

@auth_bp.route("/admin")
@admin_required
def admin_center():
    """The full admin center page (admin only)."""
    return render_template("admin.html")