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
        return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 400

    password_ok = db.verify_password(user, password)

    # Phase 14.5: failed-login lockout
    if not password_ok:
        db.increment_failed_logins(user["id"])
        if db.count_failed_logins(user["id"]) >= MAX_FAILED_LOGINS:
            db.lock_user(user["id"])
            db.log_action(
                user_id=user["id"],
                username=user["full_name"] or user["username"],
                action="قفل الحساب",
                target_type="user",
                target_id=user["id"],
                details=f"تم قفل الحساب تلقائياً بعد {MAX_FAILED_LOGINS} محاولات فاشلة",
            )
            return jsonify({"ok": False, "error": "تم قفل الحساب بسبب محاولات كثيرة — راجع المدير"}), 403
        return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 400

    # Reset the failed-login counter on success.
    db.reset_failed_logins(user["id"])
    _clear_rate_limit()

    # Phase 14.4: status / expiry gate
    if str(user["status"] or "active") == "suspended":
        return jsonify({"ok": False, "error": "الحساب موقوف — راجع المدير"}), 403
    if str(user["status"] or "active") == "pending":
        return jsonify({"ok": False, "error": "بانتظار موافقة المدير"}), 403
    if not db.is_user_active(user):
        return jsonify({"ok": False, "error": "انتهت صلاحية الدخول — راجع المدير"}), 403

    session.permanent = True
    session["user_id"] = user["id"]
    session["user_role"] = user["role"]
    session["user_name"] = user["full_name"] or user["username"]
    db.log_action(
        user_id=user["id"],
        username=user["full_name"] or user["username"],
        action="تسجيل دخول",
        details=f"تم تسجيل دخول {user['full_name'] or user['username']}",
    )
    return jsonify(
        {
            "ok": True,
            "message": "تم تسجيل الدخول بنجاح",
            "redirect": "/admin" if user["role"] == "admin" else "/",
        }
    )


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
                "created_at": u["created_at"] or "",
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