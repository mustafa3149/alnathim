"""Mobile JSON API — /api/mobile/v1 (Phase 2 of the native Android rewrite).

Token-authenticated JSON API for the native Android app (android_native/).

Reuses database.py business functions and mirrors the web dashboard's
business rules exactly (invoice generation, renewal, debt rollover,
quick-pay, payment edit/delete) so money math stays identical.

Envelope (project rule 8.1 — Arabic messages):
    success: {"ok": true,  "data": {...}}
    error:   {"ok": false, "error": {"code": "...", "message_ar": "..."}}

Auth: `Authorization: Bearer <access-token>`. Tokens are HMAC-SHA256 signed
with config.ACTIVATION_KEY_SECRET; refresh tokens rotate on refresh and are
revocable via the mobile_revoked_tokens table. RBAC is enforced server-side
on every endpoint (login → any, admin-only endpoints rejected for agents).
"""
from collections import defaultdict, deque
from datetime import datetime, date, timedelta
from functools import wraps
from time import time
import hashlib
import hmac
import logging
import secrets
import sqlite3

from flask import Blueprint, request, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash

import database as db
from config import (
    ACTIVATION_KEY_SECRET,
    AUTH_TOKEN_TTL_HOURS,
    AUTH_REFRESH_TTL_DAYS,
    MAX_FAILED_LOGINS,
    LOGIN_RATE_LIMIT_ATTEMPTS,
    LOGIN_RATE_LIMIT_WINDOW_MINUTES,
    IS_RENDER,
)
from billing_system.mikrotik_sync import sync_customer_debt

mobile_bp = Blueprint("mobile", __name__, url_prefix="/api/mobile/v1")
log = logging.getLogger(__name__)

# ── Formatters (mirror app.py so business math stays identical) ──
DATETIME_DB = "%Y-%m-%d %H:%M"
DATETIME_DB_S = "%Y-%m-%d %H:%M:%S"

# Timing-equalizer for unknown usernames (same trick as auth.py).
_DUMMY_HASH = generate_password_hash("timing-equalizer-not-a-real-password")


def _now_dt():
    """Return the current datetime with seconds/microseconds zeroed."""
    return datetime.now().replace(second=0, microsecond=0)


def _fmt_dt_db(dt):
    """Format a datetime for DB storage (passes strings through)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.strftime(DATETIME_DB)


def _to_int(value, default=0):
    """Parse an int defensively (matches app.py amount parsing)."""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return default


def _resolve_package_id(package_name):
    """Resolve a package name to its id, or None (mirrors app.py)."""
    if not package_name:
        return None
    pkg = db.get_package_by_name(package_name)
    return pkg["id"] if pkg else None


# ── Response envelope ─────────────────────────────────────────

def _ok(data=None):
    """Success envelope: {ok: true, data: ...}."""
    return jsonify({"ok": True, "data": data})


def _err(code, message_ar, status=400):
    """Error envelope: {ok: false, error: {code, message_ar}}."""
    return (
        jsonify({"ok": False, "error": {"code": code, "message_ar": message_ar}}),
        status,
    )


def _audit(action, target_type="", target_id=None, details=""):
    """Log an action performed by the current mobile user (who did what)."""
    u = getattr(g, "mobile_user", None)
    if u:
        db.log_action(
            user_id=u["id"],
            username=u["full_name"] or u["username"],
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details,
        )


# ── Signed bearer tokens (HMAC-SHA256, JWT-style) ─────────────

def _sign(payload):
    """Return the HMAC-SHA256 signature of a token body."""
    return hmac.new(
        ACTIVATION_KEY_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _issue_token(user_id, ttl_hours, typ, jti=None):
    """Mint a signed `typ.uid.exp.jti.sig` bearer token."""
    jti = jti or secrets.token_hex(16)
    exp = int(time()) + int(ttl_hours * 3600)
    body = f"{typ}.{user_id}.{exp}.{jti}"
    return f"{body}.{_sign(body)}"


def _parse_token(token):
    """Verify a token's signature/type and return its payload dict, or None."""
    parts = (token or "").split(".")
    if len(parts) != 5:
        return None
    typ, uid_s, exp_s, jti, sig = parts
    body = f"{typ}.{uid_s}.{exp_s}.{jti}"
    if not hmac.compare_digest(_sign(body), sig):
        return None
    if typ not in ("access", "refresh"):
        return None
    try:
        return {"typ": typ, "user_id": int(uid_s), "exp": int(exp_s), "jti": jti}
    except ValueError:
        return None


def _current_user():
    """Return the user row for the request's Bearer access token, or None.

    Enforces signature, expiry, revocation and the account status gate
    (suspended / expired accounts are rejected on every endpoint).
    """
    authz = request.headers.get("Authorization", "")
    if not authz.startswith("Bearer "):
        return None
    parsed = _parse_token(authz[len("Bearer "):].strip())
    if not parsed or parsed["typ"] != "access":
        return None
    if int(parsed["exp"]) <= time():
        return None
    if db.is_mobile_token_revoked(parsed["jti"]):
        return None
    user = db.get_user_by_id(parsed["user_id"])
    if not user:
        return None
    if str(user["status"] or "active") == "suspended":
        return None
    if not db.is_user_active(user):
        return None
    return user


def mobile_login_required(fn):
    """Require a valid mobile access token."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if user is None:
            return _err("unauthorized", "جلسة غير صالحة — سجل الدخول مرة أخرى", 401)
        g.mobile_user = user
        return fn(*args, **kwargs)
    return wrapper


def mobile_admin_required(fn):
    """Require a valid admin mobile access token."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _current_user()
        if user is None:
            return _err("unauthorized", "جلسة غير صالحة — سجل الدخول مرة أخرى", 401)
        if user["role"] != "admin":
            return _err("forbidden", "هذه العملية تتطلب صلاحيات المدير", 403)
        g.mobile_user = user
        return fn(*args, **kwargs)
    return wrapper


# ── Payload serializers ──────────────────────────────────────

def _user_payload(user):
    """Serialize a users row for the mobile app."""
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "full_name": user["full_name"] or "",
        "phone": user["phone"] or "",
        "status": user["status"] or "active",
        "access_expires": user["access_expires"],
    }


def _customer_payload(c):
    """Serialize a customer row (legacy alias columns) for the mobile app."""
    return {
        "id": c["id"],
        "name": c["name"] or "",
        "phone": c["phone"] or "",
        "phone2": c["phone2"] or "",
        "whatsapp_phone": c["whatsapp_phone"] or "",
        "address": c["address"] or "",
        "region": c["region"] or "",
        "package_id": c["package_id"],
        "package_name": c["package_name"] or "",
        "package_price": c["package_price"] or 0,
        "username": c["username"] or "",
        "password": c["password"] or "",
        "ip_address": c["ip_address"] or "",
        "device_type": c["device_type"] or "",
        "subscription_status": c["subscription_status"] or "",
        "is_active": bool(c["is_active"]),
        "subscription_date": c["subscription_date"],
        "renewal_date": c["renewal_date"],
        "previous_debt": c["previous_debt"] or 0,
        "notes": c["notes"] or "",
        "created_at": c["created_at"],
    }


def _invoice_payload(inv):
    """Serialize an invoice row (with remaining/is_paid computed)."""
    total = _to_int(inv["total_amount"])
    paid = min(_to_int(inv["paid_amount"]), total)
    customer_name = ""
    try:
        customer_name = inv["customer_name"] or ""
    except (KeyError, IndexError, TypeError):
        pass
    return {
        "id": inv["id"],
        "customer_id": inv["customer_id"],
        "customer_name": customer_name,
        "month": inv["month"],
        "year": inv["year"],
        "package_name": inv["package_name"] or "",
        "package_price": inv["package_price"] or 0,
        "total_amount": total,
        "paid_amount": paid,
        "remaining": total - paid,
        "is_paid": bool(inv["is_paid"]) or paid >= total,
        "previous_debt": inv["previous_debt"] or 0,
    }


def _payment_payload(p):
    """Serialize a payment row."""
    return {
        "id": p["id"],
        "invoice_id": p["invoice_id"],
        "customer_id": p["customer_id"],
        "amount": p["amount"],
        "payment_date": p["payment_date"],
        "payment_method": p["payment_method"] or "",
        "notes": p["notes"] or "",
    }


# ── AUTH ─────────────────────────────────────────────────────

@mobile_bp.route("/auth/login", methods=["POST"])
def mobile_login():
    """POST /auth/login → {token, refresh_token, expires_in, user}."""
    if _login_rate_limited():
        return _err("rate_limited", "محاولات كثيرة — حاول بعد قليل", 429)

    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    if not username or not password:
        return _err("invalid_input", "أدخل اسم المستخدم وكلمة المرور", 400)

    user = db.get_user_by_username(username)

    # Timing-safe: always run a real hash check (dummy when user unknown).
    if user is None:
        check_password_hash(_DUMMY_HASH, password)
        password_ok = False
    else:
        password_ok = db.verify_password(user, password)

    # Failed-login lockout (mirrors auth.py).
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
                return _err("locked", "تم قفل الحساب بسبب محاولات كثيرة — راجع المدير", 403)
        return _err("invalid_credentials", "بيانات الدخول غير صحيحة", 400)

    db.reset_failed_logins(user["id"])
    _clear_login_rate_limit()

    # Account status / expiry gate (mirrors auth.py).
    status = str(user["status"] or "active")
    if status == "suspended":
        return _err("suspended", "الحساب موقوف — راجع المدير", 403)
    if status == "pending":
        return _err("pending", "بانتظار موافقة المدير", 403)
    if not db.is_user_active(user):
        return _err("expired", "انتهت صلاحية الدخول — راجع المدير", 403)

    access = _issue_token(user["id"], AUTH_TOKEN_TTL_HOURS, "access")
    refresh = _issue_token(user["id"], AUTH_REFRESH_TTL_DAYS * 24, "refresh")

    db.log_action(
        user_id=user["id"],
        username=user["full_name"] or user["username"],
        action="تسجيل دخول",
        details=f"تسجيل دخول عبر التطبيق ({user['full_name'] or user['username']})",
    )

    return _ok({
        "token": access,
        "refresh_token": refresh,
        "expires_in": int(AUTH_TOKEN_TTL_HOURS * 3600),
        "user": _user_payload(user),
    })


@mobile_bp.route("/auth/register", methods=["POST"])
def mobile_register():
    """POST /auth/register → create an account (mirrors register.html).

    Accepts {full_name, username, password, phone?, invite_code?}. A valid
    invite code creates an *active* account (auto-approved, the code's use
    counter is incremented); otherwise the account is created with status
    'pending' and the admin approves it later (same as the web flow).
    Returns {auto_approved: true|false, message_ar} in the data envelope.
    """
    try:
        data = request.get_json() or {}
    except Exception:
        data = {}
    full_name = str(data.get("full_name", "")).strip()
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    phone = str(data.get("phone", "")).strip()
    invite_code = str(data.get("invite_code", "")).strip()

    if not full_name or not username or not password:
        return _err("invalid_input", "جميع الحقول المطلوبة فارغة", 400)
    if len(password) < 6:
        return _err("invalid_input", "كلمة المرور يجب أن تكون ٦ أحرف على الأقل", 400)

    try:
        result = db.create_registration(
            full_name=full_name,
            username=username,
            password=password,
            phone=phone,
            invite_code=invite_code or "",
        )
    except sqlite3.IntegrityError:
        return _err("user_exists", "اسم المستخدم موجود مسبقاً", 409)

    if result["status"] == "active":
        db.log_action(
            user_id=result["id"],
            username=full_name,
            action="تسجيل حساب",
            details=f"تم تسجيل حساب جديد عبر التطبيق برمز دعوة ({username})",
        )
        return _ok({
            "auto_approved": True,
            "message_ar": "تم تفعيل الحساب — سجل دخولك الآن",
        })

    db.log_action(
        user_id=result["id"],
        username=full_name,
        action="طلب تسجيل",
        details=f"طلب حساب جديد عبر التطبيق بانتظار موافقة المدير ({username})",
    )
    return _ok({
        "auto_approved": False,
        "message_ar": "تم استلام طلبك — بانتظار موافقة المدير",
    })


@mobile_bp.route("/auth/refresh", methods=["POST"])
def mobile_refresh():
    """POST /auth/refresh → rotate to a fresh token pair."""
    data = request.get_json() or {}
    parsed = _parse_token(data.get("refresh_token", ""))
    if not parsed or parsed["typ"] != "refresh":
        return _err("unauthorized", "رمز التحديث غير صالح", 401)
    if int(parsed["exp"]) <= time():
        return _err("unauthorized", "انتهت صلاحية رمز التحديث — سجل الدخول مرة أخرى", 401)
    if db.is_mobile_token_revoked(parsed["jti"]):
        return _err("unauthorized", "رمز التحديث ملغي — سجل الدخول مرة أخرى", 401)

    user = db.get_user_by_id(parsed["user_id"])
    if not user or str(user["status"] or "active") == "suspended" or not db.is_user_active(user):
        return _err("unauthorized", "الحساب غير نشط — راجع المدير", 403)

    # Rotate: revoke the used refresh token and mint a fresh pair.
    db.revoke_mobile_token(parsed["jti"])
    access = _issue_token(user["id"], AUTH_TOKEN_TTL_HOURS, "access")
    refresh = _issue_token(user["id"], AUTH_REFRESH_TTL_DAYS * 24, "refresh")
    return _ok({
        "token": access,
        "refresh_token": refresh,
        "expires_in": int(AUTH_TOKEN_TTL_HOURS * 3600),
        "user": _user_payload(user),
    })


@mobile_bp.route("/auth/me")
@mobile_login_required
def mobile_me():
    """GET /auth/me → current user payload (used by the app to restore a session).

    Lets the mobile app verify a saved access token and fetch the current
    user without re-sending credentials — the same role as /dashboard/summary
    but lighter (no DB aggregates).
    """
    return _ok(_user_payload(g.mobile_user))


@mobile_bp.route("/auth/logout", methods=["POST"])
@mobile_login_required
def mobile_logout():
    """POST /auth/logout → revoke the access (and refresh, if given) tokens."""
    data = request.get_json() or {}
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        parsed = _parse_token(authz[len("Bearer "):].strip())
        if parsed:
            db.revoke_mobile_token(parsed["jti"])
    parsed_refresh = _parse_token(data.get("refresh_token", ""))
    if parsed_refresh:
        db.revoke_mobile_token(parsed_refresh["jti"])
    u = g.mobile_user
    db.log_action(
        user_id=u["id"],
        username=u["full_name"] or u["username"],
        action="تسجيل خروج",
        details="تسجيل خروج من التطبيق",
    )
    return _ok({"logged_out": True})


# ── DASHBOARD ────────────────────────────────────────────────

@mobile_bp.route("/dashboard/summary")
@mobile_login_required
def mobile_dashboard_summary():
    """GET /dashboard/summary — KPIs + recent payments for the home screen."""
    now = _now_dt()
    stats = db.dashboard_stats(now.month, now.year)
    collected_today = db.total_payments_today()

    week = now + timedelta(days=7)
    expiring = db.customers_expiring_between(_fmt_dt_db(now), _fmt_dt_db(week))
    today_str_ = now.strftime("%Y-%m-%d")
    expiring_today = [
        e for e in expiring if str(e["renewal_date"] or "")[:10] == today_str_
    ]

    recent = db.list_recent_payments(limit=10)

    return _ok({
        "active_customers": stats["active_customers"],
        "total_packages": stats["total_packages"],
        "expected_income": stats["expected_income"],
        "collected": stats["collected"],
        "collected_today": collected_today,
        "total_debt": stats["total_debt"],
        "expiring_this_week": len(expiring),
        "expiring_today": len(expiring_today),
        "expiring_customers": [
            {
                "id": e["id"],
                "name": e["name"] or "",
                "package_name": e["package_name"] or "",
                "renewal_date": e["renewal_date"],
            }
            for e in expiring
        ],
        "pending_tickets": len(db.list_tickets(status="pending")),
        "recent_payments": [_payment_payload(p) for p in recent],
    })


# ── CUSTOMERS ────────────────────────────────────────────────

@mobile_bp.route("/customers")
@mobile_login_required
def mobile_customers_list():
    """GET /customers?q&region&status&debt&page&per_page — paginated list."""
    q = request.args.get("q", "").strip()
    region = request.args.get("region", "").strip()
    status = request.args.get("status", "all").strip()
    debt = request.args.get("debt", "").lower() in ("1", "true", "yes")
    page = max(1, _to_int(request.args.get("page"), 1) or 1)
    per_page = min(200, max(1, _to_int(request.args.get("per_page"), 50) or 50))

    rows = db.query_customers(search=q, status=status, region=region, debt=debt)
    total = len(rows)
    start = (page - 1) * per_page
    items = [_customer_payload(c) for c in rows[start:start + per_page]]
    return _ok({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": (total + per_page - 1) // per_page if per_page else 0,
    })


@mobile_bp.route("/customers/changes")
@mobile_login_required
def mobile_customer_changes():
    """GET /customers/changes?since=<db-datetime> — incremental sync.

    Returns customers whose updated_at is newer than the cursor (or all
    customers the first time). The app stores the newest updated_at as its
    cursor so later pulls only load what actually changed.
    """
    since = request.args.get("since", "").strip()
    changed = db.customers_changed_since(since)
    items = [_customer_payload(c) for c in changed]
    newest = ""
    for c in changed:
        ts = c["updated_at"] or c["created_at"] or ""
        if ts > newest:
            newest = ts
    return _ok({"items": items, "newest_cursor": newest, "count": len(items)})


@mobile_bp.route("/customers/<int:customer_id>")
@mobile_login_required
def mobile_customer_get(customer_id):
    """GET /customers/{id} — full customer profile."""
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)
    return _ok({"customer": _customer_payload(customer)})


@mobile_bp.route("/customers", methods=["POST"])
@mobile_login_required
def mobile_customer_create():
    """POST /customers — create a customer (mirrors /api/customers/add)."""
    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return _err("invalid_input", "الاسم مطلوب", 400)

    if db.get_customer_by_name(name):
        return _err("duplicate", "يوجد مشترك بنفس الاسم", 400)

    int_price = _to_int(data.get("package_price"))
    months = max(1, _to_int(data.get("duration_months"), 1) or 1)
    previous_debt = max(0, _to_int(data.get("previous_debt")))
    paid_in_full = bool(data.get("paid_in_full")) or previous_debt <= 0

    now = _now_dt()
    from dateutil.relativedelta import relativedelta
    package_id = _resolve_package_id(str(data.get("package_name", "")).strip())

    try:
        customer_id = db.add_customer(
            full_name=name,
            phone=str(data.get("phone", "")).strip(),
            phone2=str(data.get("phone2", "")).strip(),
            whatsapp_phone=str(data.get("whatsapp_phone", "")).strip(),
            address=str(data.get("address", "")).strip(),
            region=str(data.get("region", "")).strip(),
            package_id=package_id,
            mikrotik_username=str(data.get("username", "")).strip(),
            mikrotik_password=str(data.get("password", "")).strip(),
            nano_ip=str(data.get("ip_address", "")).strip(),
            device_type=str(data.get("device_type", "")).strip(),
            subscription_date=_fmt_dt_db(now),
            renewal_date=_fmt_dt_db(now + relativedelta(months=months)),
            status="active",
            previous_debt=previous_debt,
            notes=str(data.get("notes", "")).strip(),
        )
    except Exception as e:
        log.error("Mobile customer add failed: %s", e)
        return _err("server_error", f"خطأ: {e}", 500)

    # Single current-month invoice: package cost + carried previous debt.
    package_total = int_price * months
    total_due = package_total + previous_debt
    paid_amount = previous_debt if paid_in_full else 0
    db.add_invoice(
        customer_id=customer_id, month=now.month, year=now.year,
        package_name=str(data.get("package_name", "")).strip(),
        package_price=int_price,
        total_amount=total_due,
        paid_amount=paid_amount,
        is_paid=paid_amount >= total_due,
        previous_debt=previous_debt,
    )
    customer = db.get_customer(customer_id)
    if customer:
        sync_customer_debt(customer)
    _audit("اضافة مشترك", "customer", customer_id,
           f"تم اضافة المشترك {name} (دين سابق {previous_debt} د.ع)")
    return _ok({"customer_id": customer_id,
                "customer": _customer_payload(db.get_customer(customer_id))})


@mobile_bp.route("/customers/<int:customer_id>", methods=["PUT"])
@mobile_login_required
def mobile_customer_update(customer_id):
    """PUT /customers/{id} — edit a customer (mirrors /api/customers/edit)."""
    data = request.get_json() or {}
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)

    int_price = _to_int(data.get("package_price"))
    package_id = _resolve_package_id(str(data.get("package_name", "")).strip())

    db.update_customer(
        customer_id,
        name=str(data.get("name", customer["name"])).strip(),
        phone=str(data.get("phone", "")).strip(),
        phone2=str(data.get("phone2", "")).strip(),
        whatsapp_phone=str(data.get("whatsapp_phone", "")).strip(),
        address=str(data.get("address", "")).strip(),
        region=str(data.get("region", "")).strip(),
        notes=str(data.get("notes", "")).strip(),
        username=str(data.get("username", "")).strip(),
        password=str(data.get("password", "")).strip(),
        ip_address=str(data.get("ip_address", "")).strip(),
        device_type=str(data.get("device_type", "")).strip(),
        package_id=package_id,
        status=customer["subscription_status"],
    )
    # Keep legacy package fields on the customer's invoices consistent.
    db.update_customer_legacy_fields(
        customer_id,
        package_name=str(data.get("package_name", "")).strip(),
        package_price=int_price,
    )
    _audit("تعديل مشترك", "customer", customer_id,
           f"تم تعديل بيانات المشترك {customer['name']}")
    return _ok({"customer": _customer_payload(db.get_customer(customer_id))})


@mobile_bp.route("/customers/<int:customer_id>/toggle", methods=["POST"])
@mobile_login_required
def mobile_customer_toggle(customer_id):
    """POST /customers/{id}/toggle — flip is_active (mirrors web toggle)."""
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)
    db.toggle_customer(customer_id)
    _audit("تبديل تفعيل مشترك", "customer", customer_id,
           f"تم تبديل حالة المشترك {customer['name']}")
    return _ok({"customer": _customer_payload(db.get_customer(customer_id))})


@mobile_bp.route("/customers/<int:customer_id>/renew", methods=["POST"])
@mobile_login_required
def mobile_customer_renew(customer_id):
    """POST /customers/{id}/renew — renew N months + carry debt (mirrors web)."""
    data = request.get_json() or {}
    try:
        months = int(data.get("months", 1))
        if months <= 0:
            months = 1
    except (ValueError, TypeError):
        months = 1

    from dateutil.relativedelta import relativedelta
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)

    now = _now_dt()
    base = None
    if customer["renewal_date"]:
        for fmt in (DATETIME_DB, DATETIME_DB_S):
            try:
                base = datetime.strptime(str(customer["renewal_date"]), fmt)
                break
            except ValueError:
                continue
    if base is None or base < now:
        base = now

    new_renewal = base + relativedelta(months=months)
    try:
        db.update_customer(
            customer_id,
            renewal_date=_fmt_dt_db(new_renewal),
            subscription_status="active",
            status="active",
        )
        renew_total = (customer["package_price"] or 0) * months
        carried_debt = db.customer_unpaid_debt(
            customer_id, up_to_month=now.month, up_to_year=now.year
        )
        total_due = renew_total + carried_debt
        # Debt rollover rule: if a current-month invoice already exists (e.g.
        # from quick-pay), merge the carried debt into it instead of inserting
        # a duplicate (invoices have a UNIQUE(customer_id, month, year)).
        existing_invoice = db.get_customer_invoice(customer_id, now.month, now.year)
        if existing_invoice:
            old_total = existing_invoice["total_amount"] or 0
            old_paid = existing_invoice["paid_amount"] or 0
            new_total = (existing_invoice["package_price"] or 0) + carried_debt
            # The previously paid amount must not exceed the new total.
            if old_paid > new_total:
                old_paid = new_total
            db.update_invoice(
                existing_invoice["id"],
                total_amount=new_total,
                paid_amount=old_paid,
                is_paid=old_paid >= new_total,
                previous_debt=carried_debt,
            )
            invoices_generated = 0
        else:
            db.add_invoice(
                customer_id=customer_id, month=now.month, year=now.year,
                package_name=customer["package_name"] or "",
                package_price=customer["package_price"] or 0,
                total_amount=total_due, paid_amount=0, is_paid=False,
                previous_debt=carried_debt,
            )
            invoices_generated = 1
        refreshed = db.get_customer(customer_id)
        if refreshed:
            sync_customer_debt(refreshed)
        _audit("تجديد اشتراك", "customer", customer_id,
               f"تم تجديد اشتراك {customer['name']} لمدة {months} شهر "
               f"(اجمالي {total_due} د.ع)")
    except Exception as e:
        log.error("Mobile renew failed: %s", e)
        return _err("server_error", str(e), 500)

    return _ok({
        "renewal_date": _fmt_dt_db(new_renewal),
        "invoices_generated": invoices_generated,
        "total_amount": total_due,
        "carried_debt": carried_debt,
        "customer": _customer_payload(db.get_customer(customer_id)),
    })


@mobile_bp.route("/customers/<int:customer_id>", methods=["DELETE"])
@mobile_admin_required
def mobile_customer_delete(customer_id):
    """DELETE /customers/{id} — delete a customer (admin only)."""
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)
    try:
        db.delete_customer(customer_id)
    except Exception as e:
        return _err("server_error", f"فشل الحذف: {e}", 500)
    _audit("حذف مشترك", "customer", customer_id,
           f"تم حذف المشترك {customer['name']}")
    return _ok({"deleted": True})


@mobile_bp.route("/customers/<int:customer_id>/history")
@mobile_login_required
def mobile_customer_history(customer_id):
    """GET /customers/{id}/history — invoices + payments (mirrors web)."""
    if not db.get_customer(customer_id):
        return _err("not_found", "المشترك غير موجود", 404)

    invoices = db.list_customer_invoices(customer_id)
    payments = db.list_customer_payments(customer_id)

    inv_list = []
    for inv in invoices:
        extras = db.list_invoice_extras(inv["id"])
        paid = min(float(inv["paid_amount"] or 0), float(inv["total_amount"] or 0))
        inv_list.append({
            "id": inv["id"],
            "month": inv["month"],
            "year": inv["year"],
            "package_name": inv["package_name"] or "",
            "package_price": inv["package_price"] or 0,
            "total_amount": inv["total_amount"],
            "paid_amount": paid,
            "is_paid": bool(inv["is_paid"]) or paid >= float(inv["total_amount"] or 0),
            "previous_debt": inv["previous_debt"] or 0,
            "extras": [
                {"id": e["id"], "item_name": e["item_name"], "item_price": e["item_price"]}
                for e in extras
            ],
        })

    return _ok({"invoices": inv_list, "payments": [_payment_payload(p) for p in payments]})


@mobile_bp.route("/customers/<int:customer_id>/signal")
@mobile_login_required
def mobile_customer_signal(customer_id):
    """GET /customers/{id}/signal — cached SNMP signal for the customer's IP."""
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)
    ip = (customer["ip_address"] or "").strip()
    if not ip:
        return _ok({"signal": None, "ip": ""})
    cached = db.get_cached_signal(ip)
    return _ok({"signal": dict(cached) if cached else None, "ip": ip})


# ── BILLING ──────────────────────────────────────────────────

@mobile_bp.route("/invoices")
@mobile_login_required
def mobile_invoices_list():
    """GET /invoices?customer_id&month&year&search — invoice list."""
    customer_id = request.args.get("customer_id")
    if customer_id is not None:
        rows = db.list_customer_invoices(_to_int(customer_id))
    else:
        month = _to_int(request.args.get("month"), _now_dt().month)
        year = _to_int(request.args.get("year"), _now_dt().year)
        search = request.args.get("search", "").strip()
        rows = db.list_invoices(month, year, search=search)
    return _ok({"items": [_invoice_payload(inv) for inv in rows]})


@mobile_bp.route("/invoices/<int:invoice_id>")
@mobile_login_required
def mobile_invoice_get(invoice_id):
    """GET /invoices/{id} — invoice detail + payments + extras."""
    inv = db.get_invoice(invoice_id)
    if not inv:
        return _err("not_found", "الفاتورة غير موجودة", 404)
    extras = db.list_invoice_extras(invoice_id)
    payments = db.list_invoice_payments(invoice_id)
    return _ok({
        "invoice": _invoice_payload(inv),
        "extras": [
            {"id": e["id"], "item_name": e["item_name"], "item_price": e["item_price"]}
            for e in extras
        ],
        "payments": [_payment_payload(p) for p in payments],
    })


@mobile_bp.route("/payments/current-invoice/<int:customer_id>")
@mobile_login_required
def mobile_current_invoice(customer_id):
    """GET /payments/current-invoice/{customer_id} — this month's invoice."""
    if not db.get_customer(customer_id):
        return _err("not_found", "المشترك غير موجود", 404)
    now = _now_dt()
    invoice = db.get_customer_invoice(customer_id, now.month, now.year)
    if invoice:
        return _ok({
            "invoice": _invoice_payload(invoice),
            "remaining": (invoice["total_amount"] or 0) - (invoice["paid_amount"] or 0),
        })
    return _ok({"invoice": None, "remaining": 0})


@mobile_bp.route("/payments", methods=["POST"])
@mobile_login_required
def mobile_payment_create():
    """POST /payments — record a payment on an invoice (mirrors /api/payment/make)."""
    data = request.get_json() or {}
    invoice_id = data.get("invoice_id")
    try:
        amount = float(data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0
    if not invoice_id or amount <= 0:
        return _err("invalid_input", "البيانات غير صالحة", 400)

    invoice = db.get_invoice(invoice_id)
    if not invoice:
        return _err("not_found", "الفاتورة غير موجودة", 404)

    remaining = (invoice["total_amount"] or 0) - (invoice["paid_amount"] or 0)
    if amount > remaining:
        amount = remaining
    if amount <= 0:
        return _err("already_paid", "الفاتورة مدفوعة بالكامل", 400)

    new_paid = (invoice["paid_amount"] or 0) + amount
    is_paid = new_paid >= (invoice["total_amount"] or 0)
    db.update_invoice(invoice_id, paid_amount=new_paid, is_paid=is_paid)

    pay_date_str = data.get("payment_date", _now_dt().strftime("%Y-%m-%d"))
    try:
        pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pay_date = date.today().isoformat()

    payment_id = db.add_payment(
        invoice_id=invoice_id, customer_id=invoice["customer_id"],
        amount=amount, payment_date=pay_date,
        payment_method=data.get("payment_method", "نقدي"),
        notes=str(data.get("notes", "")).strip(),
    )
    _audit("تسديد فاتورة", "payment", invoice_id,
           f"تم تسديد {amount} د.ع على فاتورة رقم {invoice_id}")
    refreshed = db.get_customer(invoice["customer_id"])
    if refreshed:
        sync_customer_debt(refreshed)
    return _ok({
        "payment_id": payment_id,
        "invoice": _invoice_payload(db.get_invoice(invoice_id)),
    })


@mobile_bp.route("/payments")
@mobile_login_required
def mobile_payments_list():
    """GET /payments?limit&search — recent payments (with customer names)."""
    limit = min(200, max(1, _to_int(request.args.get("limit"), 50) or 50))
    search = request.args.get("search", "").strip()
    method = request.args.get("method", "").strip()
    rows = db.list_recent_payments(search=search, method=method, limit=limit)
    items = []
    for p in rows:
        payload = _payment_payload(p)
        try:
            payload["customer_name"] = p["customer_name"] or ""
        except (KeyError, IndexError, TypeError):
            payload["customer_name"] = ""
        items.append(payload)
    return _ok({"items": items})


@mobile_bp.route("/payments/<int:payment_id>", methods=["PUT"])
@mobile_admin_required
def mobile_payment_edit(payment_id):
    """PUT /payments/{id} — edit a payment (admin, mirrors web edit)."""
    data = request.get_json() or {}
    payment = db.get_payment(payment_id)
    if not payment:
        return _err("not_found", "الدفعة غير موجودة", 404)

    try:
        new_amount = float(data.get("amount", 0))
    except (ValueError, TypeError):
        new_amount = 0
    if new_amount <= 0:
        return _err("invalid_input", "المبلغ غير صالح", 400)

    invoice = db.get_invoice(payment["invoice_id"])
    if not invoice:
        return _err("not_found", "الفاتورة غير موجودة", 404)

    old_amount = payment["amount"]
    new_paid = (invoice["paid_amount"] or 0) - old_amount + new_amount
    new_paid = min(new_paid, invoice["total_amount"])
    new_paid = max(new_paid, 0)
    is_paid = new_paid >= (invoice["total_amount"] or 0)
    db.update_invoice(invoice["id"], paid_amount=new_paid, is_paid=is_paid)

    pay_date = payment["payment_date"]
    try:
        pay_date = datetime.strptime(data.get("payment_date", ""), "%Y-%m-%d").date().isoformat()
    except ValueError:
        pass

    db.update_payment(
        payment_id,
        amount=new_amount,
        payment_date=pay_date,
        payment_method=data.get("payment_method", payment["payment_method"]),
        notes=str(data.get("notes", "")).strip(),
    )
    _audit("تعديل دفعة", "payment", payment_id,
           f"تم تعديل دفعة بقيمة {new_amount} د.ع")
    refreshed = db.get_customer(invoice["customer_id"])
    if refreshed:
        sync_customer_debt(refreshed)
    return _ok({"payment": _payment_payload(db.get_payment(payment_id)),
                "invoice": _invoice_payload(db.get_invoice(invoice["id"]))})


@mobile_bp.route("/payments/<int:payment_id>", methods=["DELETE"])
@mobile_admin_required
def mobile_payment_delete(payment_id):
    """DELETE /payments/{id} — delete a payment (admin, mirrors web delete)."""
    payment = db.get_payment(payment_id)
    if not payment:
        return _err("not_found", "الدفعة غير موجودة", 404)

    invoice = db.get_invoice(payment["invoice_id"])
    if invoice:
        new_paid = (invoice["paid_amount"] or 0) - payment["amount"]
        if new_paid < 0:
            new_paid = 0
        is_paid = new_paid >= (invoice["total_amount"] or 0)
        db.update_invoice(invoice["id"], paid_amount=new_paid, is_paid=is_paid)

    db.delete_payment(payment_id)
    _audit("حذف دفعة", "payment", payment_id, "تم حذف دفعة")
    return _ok({"deleted": True})


@mobile_bp.route("/quick-pay/<int:customer_id>", methods=["POST"])
@mobile_login_required
def mobile_quick_pay(customer_id):
    """POST /quick-pay/{customer_id} — auto-creates the current invoice if needed."""
    data = request.get_json() or {}
    customer = db.get_customer(customer_id)
    if not customer:
        return _err("not_found", "المشترك غير موجود", 404)

    now = _now_dt()
    month, year = now.month, now.year

    invoice = db.get_customer_invoice(customer_id, month, year)
    if invoice is None:
        total = customer["package_price"] or 0
        invoice_id = db.add_invoice(
            customer_id=customer_id, month=month, year=year,
            package_name=customer["package_name"] or "",
            package_price=customer["package_price"] or 0,
            total_amount=total, paid_amount=0, is_paid=False,
        )
        invoice = db.get_invoice(invoice_id)

    remaining = (invoice["total_amount"] or 0) - (invoice["paid_amount"] or 0)
    if remaining <= 0:
        return _err("already_paid", "الفاتورة مدفوعة بالكامل", 400)

    try:
        pay_amount = float(data.get("amount")) if data.get("amount") else remaining
    except (ValueError, TypeError):
        pay_amount = remaining
    if pay_amount > remaining:
        pay_amount = remaining
    if pay_amount <= 0:
        return _err("invalid_amount", "المبلغ غير صالح", 400)

    new_paid = (invoice["paid_amount"] or 0) + pay_amount
    is_paid = new_paid >= (invoice["total_amount"] or 0)
    db.update_invoice(invoice["id"], paid_amount=new_paid, is_paid=is_paid)

    pay_date_str = data.get("payment_date", now.strftime("%Y-%m-%d"))
    try:
        pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date().isoformat()
    except ValueError:
        pay_date = date.today().isoformat()

    db.add_payment(
        invoice_id=invoice["id"], customer_id=customer_id,
        amount=pay_amount, payment_date=pay_date,
        payment_method=data.get("payment_method", "نقدي"),
        notes=str(data.get("notes", "")).strip(),
    )
    _audit("دفع", "payment", invoice["id"],
           f"تم استلام {pay_amount} د.ع من {customer['name']}")
    refreshed = db.get_customer(customer_id)
    if refreshed:
        sync_customer_debt(refreshed)
    return _ok({
        "invoice": _invoice_payload(db.get_invoice(invoice["id"])),
        "amount": pay_amount,
    })


# ── DEBTS & REMINDERS ────────────────────────────────────────

@mobile_bp.route("/debts")
@mobile_login_required
def mobile_debts():
    """GET /debts — per-customer unpaid totals (mirrors the debts page)."""
    rows = db.debts_summary()
    return _ok({
        "items": [
            {
                "customer_id": r["id"],
                "name": r["name"],
                "phone": r["phone"],
                "region": r["region"],
                "package_name": r["package_name"] or "",
                "package_price": r["package_price"] or 0,
                "total_debt": r["total_debt"] or 0,
                "unpaid_count": r["unpaid_count"] or 0,
            }
            for r in rows
        ]
    })


@mobile_bp.route("/reminders")
@mobile_login_required
def mobile_reminders():
    """GET /reminders — customers with debt or whose renewal already passed."""
    rows = db.customers_with_debt_or_expired()
    return _ok({
        "items": [
            {
                "customer_id": c["id"],
                "name": c["name"],
                "phone": c["phone"],
                "whatsapp_phone": c["whatsapp_phone"] or "",
                "phone2": c["phone2"] or "",
                "package_name": c["package_name"] or "",
                "package_price": c["package_price"] or 0,
                "renewal_date": c["renewal_date"],
                "total_debt": c["total_debt"],
                "expired": bool(c["renewal_date"]) and str(c["renewal_date"]) < db.now_str(),
            }
            for c in rows
        ]
    })


# ── EXPENSES ─────────────────────────────────────────────────

@mobile_bp.route("/expenses")
@mobile_login_required
def mobile_expenses_list():
    """GET /expenses?month&year&search&category — expenses for a month."""
    now = _now_dt()
    month = _to_int(request.args.get("month"), now.month)
    year = _to_int(request.args.get("year"), now.year)
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    rows = db.list_expenses(month, year, search=search, category=category)
    return _ok({
        "month": month,
        "year": year,
        "total": db.total_expenses(month, year),
        "items": [
            {
                "id": e["id"],
                "expense_date": e["expense_date"],
                "category": e["category"],
                "amount": e["amount"],
                "description": e["description"] or "",
                "recipient_name": e["recipient_name"] or "",
                "subscriber_id": e["subscriber_id"],
                "subscriber_name": e["subscriber_name"] or "",
            }
            for e in rows
        ],
    })


@mobile_bp.route("/expenses", methods=["POST"])
@mobile_login_required
def mobile_expense_create():
    """POST /expenses — add an expense (mirrors the web add route)."""
    data = request.get_json() or {}
    try:
        amount = float(data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0
    if amount <= 0:
        return _err("invalid_input", "المبلغ يجب أن يكون أكبر من صفر", 400)
    category = str(data.get("category", "أخرى")).strip()
    if not category:
        return _err("invalid_input", "التصنيف مطلوب", 400)

    try:
        expense_date = datetime.strptime(data.get("expense_date", ""), "%Y-%m-%d").date().isoformat()
    except ValueError:
        expense_date = date.today().isoformat()

    subscriber_id = data.get("subscriber_id")
    if subscriber_id in ("", None, "null"):
        subscriber_id = None

    expense_id = db.add_expense(
        expense_date=expense_date,
        category=category,
        amount=amount,
        description=str(data.get("description", "")).strip(),
        recipient_name=str(data.get("recipient_name", "")).strip(),
        subscriber_id=subscriber_id,
    )
    _audit("اضافة مصروف", "expense", expense_id,
           f"تم اضافة مصروف {category} بقيمة {amount} د.ع")
    return _ok({"expense_id": expense_id})


@mobile_bp.route("/expenses/<int:expense_id>", methods=["PUT"])
@mobile_login_required
def mobile_expense_edit(expense_id):
    """PUT /expenses/{id} — edit an expense."""
    expense = db.get_expense(expense_id)
    if not expense:
        return _err("not_found", "المصروف غير موجود", 404)
    data = request.get_json() or {}
    fields = {}
    if data.get("expense_date"):
        try:
            fields["expense_date"] = datetime.strptime(
                data["expense_date"], "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass
    if data.get("category"):
        fields["category"] = str(data["category"]).strip()
    if data.get("amount") is not None:
        try:
            fields["amount"] = float(data["amount"])
        except (ValueError, TypeError):
            pass
    if data.get("description") is not None:
        fields["description"] = str(data["description"]).strip()
    if data.get("recipient_name") is not None:
        fields["recipient_name"] = str(data["recipient_name"]).strip()
    if data.get("subscriber_id") is not None:
        sid = data["subscriber_id"]
        fields["subscriber_id"] = None if sid in ("", "null") else sid
    db.update_expense(expense_id, **fields)
    _audit("تعديل مصروف", "expense", expense_id, "تم تعديل مصروف")
    return _ok({"expense_id": expense_id})


@mobile_bp.route("/expenses/<int:expense_id>", methods=["DELETE"])
@mobile_admin_required
def mobile_expense_delete(expense_id):
    """DELETE /expenses/{id} — delete an expense (admin only)."""
    if not db.get_expense(expense_id):
        return _err("not_found", "المصروف غير موجود", 404)
    db.delete_expense(expense_id)
    _audit("حذف مصروف", "expense", expense_id, "تم حذف مصروف")
    return _ok({"deleted": True})


# ── MAINTENANCE TICKETS ──────────────────────────────────────

@mobile_bp.route("/tickets")
@mobile_login_required
def mobile_tickets_list():
    """GET /tickets?status&search — maintenance tickets."""
    status = request.args.get("status", "all").strip()
    search = request.args.get("search", "").strip()
    rows = db.list_tickets(status=status, search=search)
    return _ok({
        "items": [
            {
                "id": t["id"],
                "customer_id": t["customer_id"],
                "customer_name": t["customer_name"] or "",
                "customer_phone": t["customer_phone"] or "",
                "issue_description": t["issue_description"],
                "status": t["status"],
                "created_at": t["created_at"],
                "resolved_at": t["resolved_at"],
            }
            for t in rows
        ]
    })


@mobile_bp.route("/tickets", methods=["POST"])
@mobile_login_required
def mobile_ticket_create():
    """POST /tickets — create a maintenance ticket (mirrors web add)."""
    data = request.get_json() or {}
    customer_id = data.get("customer_id")
    issue = str(data.get("issue_description", "")).strip()
    if not customer_id or not issue:
        return _err("invalid_input", "المشترك ووصف المشكلة مطلوبان", 400)
    if not db.get_customer(_to_int(customer_id)):
        return _err("not_found", "المشترك غير موجود", 404)
    ticket_id = db.add_ticket(_to_int(customer_id), issue)
    _audit("اضافة طلب صيانة", "ticket", ticket_id, f"تم اضافة طلب صيانة: {issue[:50]}")
    return _ok({"ticket_id": ticket_id})


@mobile_bp.route("/tickets/<int:ticket_id>/status", methods=["PUT"])
@mobile_login_required
def mobile_ticket_status(ticket_id):
    """PUT /tickets/{id}/status — toggle pending/resolved (mirrors web toggle)."""
    if not db.get_ticket(ticket_id):
        return _err("not_found", "طلب الصيانة غير موجود", 404)
    new_status = db.toggle_ticket(ticket_id)
    _audit("تغيير حالة طلب صيانة", "ticket", ticket_id,
           f"تم تغيير حالة الطلب إلى {new_status}")
    return _ok({"status": new_status})


# ── PACKAGES ─────────────────────────────────────────────────

@mobile_bp.route("/packages")
@mobile_login_required
def mobile_packages_list():
    """GET /packages — all ISP packages."""
    return _ok({"items": [dict(p) for p in db.list_packages()]})


@mobile_bp.route("/packages", methods=["POST"])
@mobile_admin_required
def mobile_package_create():
    """POST /packages — add a package (admin only)."""
    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    try:
        price = int(float(data.get("price", 0)))
    except (ValueError, TypeError):
        price = 0
    if not name:
        return _err("invalid_input", "اسم الباقة مطلوب", 400)
    package_id = db.add_package(name, price, speed=str(data.get("speed", "")).strip())
    _audit("اضافة باقة", "package", package_id, f"تم اضافة الباقة {name}")
    return _ok({"package_id": package_id})


@mobile_bp.route("/packages/<int:package_id>", methods=["PUT"])
@mobile_admin_required
def mobile_package_edit(package_id):
    """PUT /packages/{id} — edit a package (admin only)."""
    if not db.get_package(package_id):
        return _err("not_found", "الباقة غير موجودة", 404)
    data = request.get_json() or {}
    fields = {}
    if data.get("name") is not None:
        fields["name"] = str(data["name"]).strip()
    if data.get("price") is not None:
        try:
            fields["price"] = int(float(data["price"]))
        except (ValueError, TypeError):
            pass
    if data.get("speed") is not None:
        fields["speed"] = str(data["speed"]).strip()
    db.update_package(package_id, **fields)
    _audit("تعديل باقة", "package", package_id, "تم تعديل باقة")
    return _ok({"package_id": package_id})


@mobile_bp.route("/packages/<int:package_id>", methods=["DELETE"])
@mobile_admin_required
def mobile_package_delete(package_id):
    """DELETE /packages/{id} — delete a package (admin only)."""
    if not db.get_package(package_id):
        return _err("not_found", "الباقة غير موجودة", 404)
    db.delete_package(package_id)
    _audit("حذف باقة", "package", package_id, "تم حذف باقة")
    return _ok({"deleted": True})


# ── SETTINGS (admin) ─────────────────────────────────────────

@mobile_bp.route("/settings")
@mobile_admin_required
def mobile_settings_get():
    """GET /settings — key-value settings (admin only)."""
    return _ok({"settings": db.get_settings()})


@mobile_bp.route("/settings", methods=["PUT"])
@mobile_admin_required
def mobile_settings_put():
    """PUT /settings — upsert one or more settings: {key: value}."""
    data = request.get_json() or {}
    updated = []
    for key, value in data.items():
        if not isinstance(value, str):
            value = str(value)
        db.set_setting(str(key), value)
        updated.append(str(key))
    _audit("تعديل الاعدادات", "settings", None, f"تم تعديل الإعدادات: {', '.join(updated)}")
    return _ok({"updated": updated})


@mobile_bp.route("/generator-info")
@mobile_admin_required
def mobile_generator_info_get():
    """GET /generator-info — company profile (admin only)."""
    info = db.get_generator_info()
    return _ok({"generator_info": dict(info) if info else None})


@mobile_bp.route("/generator-info", methods=["PUT"])
@mobile_admin_required
def mobile_generator_info_put():
    """PUT /generator-info — update company profile (admin only)."""
    data = request.get_json() or {}
    db.update_generator_info(
        owner_name=data.get("owner_name"),
        phone=data.get("phone"),
        address=data.get("address"),
        footer_note=data.get("footer_note"),
    )
    _audit("تعديل بيانات الشركة", "generator_info", 1, "تم تعديل بيانات الشركة")
    return _ok({"generator_info": dict(db.get_generator_info())})


# ── ADMIN CENTER ─────────────────────────────────────────────

@mobile_bp.route("/team")
@mobile_admin_required
def mobile_team_list():
    """GET /team — all users (admin only)."""
    rows = db.list_users()
    return _ok({
        "items": [
            {
                "id": u["id"],
                "username": u["username"],
                "role": u["role"],
                "full_name": u["full_name"],
                "phone": u["phone"],
                "status": u["status"],
                "access_expires": u["access_expires"],
            }
            for u in rows
        ]
    })


@mobile_bp.route("/team", methods=["POST"])
@mobile_admin_required
def mobile_team_create():
    """POST /team — create a team member (admin only)."""
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    role = data.get("role", "agent") if data.get("role") in ("admin", "agent") else "agent"
    if not username:
        return _err("invalid_input", "اسم المستخدم مطلوب", 400)
    if len(password) < 6:
        return _err("invalid_input", "كلمة المرور يجب أن تكون ٦ أحرف على الأقل", 400)
    try:
        user_id = db.create_user(
            username, password, role,
            full_name=str(data.get("full_name", "")).strip(),
            phone=str(data.get("phone", "")).strip(),
        )
    except Exception:
        return _err("duplicate", "اسم المستخدم مستخدم مسبقاً", 400)
    _audit("اضافة مستخدم", "user", user_id, f"تم اضافة المستخدم {username}")
    return _ok({"user_id": user_id})


@mobile_bp.route("/team/<int:user_id>/status", methods=["PUT"])
@mobile_admin_required
def mobile_team_status(user_id):
    """PUT /team/{id}/status — activate/suspend a team member (admin only)."""
    data = request.get_json() or {}
    status = data.get("status", "")
    if status not in ("active", "suspended"):
        return _err("invalid_input", "حالة غير صالحة", 400)
    if user_id == g.mobile_user["id"] and status == "suspended":
        return _err("invalid_input", "لا يمكنك إيقاف حسابك الحالي", 400)
    db.set_user_status(user_id, status)
    _audit("تغيير حالة مستخدم", "user", user_id, f"تم تغيير حالة المستخدم إلى {status}")
    return _ok({"user_id": user_id, "status": status})


@mobile_bp.route("/audit")
@mobile_admin_required
def mobile_audit_log():
    """GET /audit?limit — recent audit entries (admin only)."""
    limit = min(500, max(1, _to_int(request.args.get("limit"), 200)))
    rows = db.list_audit_logs(limit=limit)
    return _ok({"items": [dict(r) for r in rows]})


@mobile_bp.route("/backup", methods=["POST"])
@mobile_admin_required
def mobile_backup():
    """POST /backup — snapshot the SQLite database (admin only)."""
    try:
        import sqlite3
        from config import LOCAL_DB_PATH, USE_TURSO
    except Exception:
        return _err("server_error", "تعذر انشاء نسخة احتياطية", 500)
    if USE_TURSO:
        return _err("unsupported", "النسخ الاحتياطي غير مدعوم على قاعدة Turso", 501)

    backup_path = LOCAL_DB_PATH + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        src = sqlite3.connect(LOCAL_DB_PATH)
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
    except Exception as e:
        return _err("server_error", f"فشل النسخ الاحتياطي: {e}", 500)
    _audit("نسخة احتياطية", "database", None, f"تم إنشاء نسخة احتياطية: {backup_path}")
    return _ok({"backup_path": backup_path})


@mobile_bp.route("/sync/pull", methods=["POST"])
@mobile_admin_required
def mobile_sync_pull():
    """POST /sync/pull — pull MikroTik router data + push to cloud (admin)."""
    try:
        from billing_system.mikrotik_sync import sync_pull_router, sync_push_cloud
        pull = sync_pull_router(dry_run=False)
        push = {}
        if pull.get("secrets", 0) > 0:
            push = sync_push_cloud()
    except Exception as e:
        log.error("Mobile sync pull failed: %s", e)
        return _err("server_error", f"فشل المزامنة: {e}", 500)
    _audit("سحب بيانات", "database", None, "تم سحب بيانات الراوتر ومزامنة السحابة")
    return _ok({"pull": pull, "push": push, "message": "تم سحب بيانات الراوتر ومزامنة السحابة"})


@mobile_bp.route("/sync/push", methods=["POST"])
@mobile_admin_required
def mobile_sync_push():
    """POST /sync/push — accept a full DB snapshot (admin only)."""
    data = request.get_json(silent=True) or {}
    customers = data.get("customers") or []
    packages = data.get("packages") or []
    upserted = 0
    for pkg in packages:
        name = str(pkg.get("name", "")).strip()
        if not name:
            continue
        existing = db.get_package_by_name(name)
        if existing:
            db.update_package(existing["id"], price=pkg.get("price"), speed=pkg.get("speed"))
        else:
            db.add_package(name, pkg.get("price", 0), speed=str(pkg.get("speed", "")).strip())
    for c in customers:
        name = str(c.get("name", "")).strip()
        if not name:
            continue
        try:
            package_id = _resolve_package_id(c.get("package_name", ""))
        except Exception:
            package_id = None
        try:
            existing = db.get_customer_by_name(name)
            if existing:
                db.update_customer(
                    existing["id"],
                    phone=str(c.get("phone", "")).strip(),
                    region=str(c.get("region", "")).strip(),
                    package_id=package_id,
                    ip_address=str(c.get("ip_address", "")).strip(),
                    device_type=str(c.get("device_type", "")).strip(),
                )
            else:
                from dateutil.relativedelta import relativedelta
                now = _now_dt()
                months = max(1, _to_int(c.get("duration_months"), 1) or 1)
                db.add_customer(
                    full_name=name,
                    phone=str(c.get("phone", "")).strip(),
                    phone2=str(c.get("phone2", "")).strip(),
                    whatsapp_phone=str(c.get("whatsapp_phone", "")).strip(),
                    address=str(c.get("address", "")).strip(),
                    region=str(c.get("region", "")).strip(),
                    package_id=package_id,
                    mikrotik_username=str(c.get("username", "")).strip(),
                    mikrotik_password=str(c.get("password", "")).strip(),
                    nano_ip=str(c.get("ip_address", "")).strip(),
                    device_type=str(c.get("device_type", "")).strip(),
                    subscription_date=_fmt_dt_db(now),
                    renewal_date=_fmt_dt_db(now + relativedelta(months=months)),
                    status="active",
                )
            upserted += 1
        except Exception as e:
            log.error("Mobile sync push customer %s failed: %s", name, e)
    _audit("مزامنة بيانات", "database", None, f"تم استلام لقطة بيانات ({upserted} مشترك)")
    return _ok({"upserted": upserted})


@mobile_bp.route("/tower-connection")
@mobile_admin_required
def mobile_tower_connection_get():
    """GET /tower-connection — stored MikroTik/OLT/SNMP settings (admin)."""
    s = db.get_settings()
    keys = [
        "mikrotik_host", "mikrotik_user", "mikrotik_port", "mikrotik_ssl",
        "olt_ip", "olt_snmp_community", "snmp_community", "snmp_port",
        "snmp_timeout", "snmp_retries", "oid_onu_rx", "oid_onu_tx",
        "oid_ubnt_signal", "oid_ubnt_ccq", "oid_mikrotik_signal",
    ]
    return _ok({"tower_connection": {k: s.get(k, "") for k in keys}})


@mobile_bp.route("/tower-connection", methods=["PUT"])
@mobile_admin_required
def mobile_tower_connection_put():
    """PUT /tower-connection — save MikroTik/OLT/SNMP settings (admin).

    Mirrors app.py api_tower_connection_save (validation + audit).
    """
    from network_tools.ping import validate_host

    data = request.get_json() or {}
    mikrotik_host = data.get("mikrotik_host", "").strip()
    if mikrotik_host and not validate_host(mikrotik_host):
        return _err("invalid_input", "عنوان MikroTik غير صالح", 400)
    olt_ip = data.get("olt_ip", "").strip()
    if olt_ip and not validate_host(olt_ip):
        return _err("invalid_input", "عنوان OLT غير صالح", 400)

    try:
        port = int(data.get("mikrotik_port", 8728) or 8728)
        if not 1 <= port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        return _err("invalid_input", "منفذ MikroTik غير صالح", 400)

    try:
        snmp_port = int(data.get("snmp_port", 161) or 161)
        if not 1 <= snmp_port <= 65535:
            raise ValueError
    except (ValueError, TypeError):
        return _err("invalid_input", "منفذ SNMP غير صالح", 400)

    try:
        snmp_timeout = float(data.get("snmp_timeout", 2.0) or 2.0)
        if not 0.5 <= snmp_timeout <= 30:
            raise ValueError
    except (ValueError, TypeError):
        return _err("invalid_input", "مهلة SNMP غير صالحة", 400)

    try:
        snmp_retries = int(data.get("snmp_retries", 1) or 1)
        if not 0 <= snmp_retries <= 10:
            raise ValueError
    except (ValueError, TypeError):
        return _err("invalid_input", "عدد محاولات SNMP غير صالح", 400)

    keys = {
        "mikrotik_host": mikrotik_host,
        "mikrotik_user": data.get("mikrotik_user", "").strip(),
        "mikrotik_password": data.get("mikrotik_password", ""),
        "mikrotik_port": str(port),
        "mikrotik_ssl": "1" if data.get("mikrotik_ssl") else "0",
        "olt_ip": olt_ip,
        "olt_snmp_community": data.get("olt_snmp_community", "").strip(),
        "snmp_community": data.get("snmp_community", "public").strip(),
        "snmp_port": str(snmp_port),
        "snmp_timeout": str(snmp_timeout),
        "snmp_retries": str(snmp_retries),
        "oid_onu_rx": data.get("oid_onu_rx", "").strip(),
        "oid_onu_tx": data.get("oid_onu_tx", "").strip(),
        "oid_ubnt_signal": data.get("oid_ubnt_signal", "").strip(),
        "oid_ubnt_ccq": data.get("oid_ubnt_ccq", "").strip(),
        "oid_mikrotik_signal": data.get("oid_mikrotik_signal", "").strip(),
    }
    for key, value in keys.items():
        db.set_setting(key, value)
    _audit("حفظ اعدادات اللوحة", "settings", None,
           f"تم حفظ اعدادات ربط اللوحة (MikroTik: {mikrotik_host})")
    return _ok({"message": "تم حفظ اعدادات اللوحة بنجاح"})


@mobile_bp.route("/tower-test", methods=["POST"])
@mobile_admin_required
def mobile_tower_test():
    """POST /tower-test — live connection test to the saved MikroTik (admin)."""
    try:
        from mikrotik_api.config import load_mikrotik_config
        from mikrotik_api.mikrotik_manager import MikroTikManager
        cfg = load_mikrotik_config()
        if not cfg.get("host"):
            return _err("invalid_input", "لم يتم حفظ عنوان MikroTik بعد", 400)
        manager = MikroTikManager(
            host=cfg["host"],
            username=cfg["username"],
            password=cfg["password"],
            port=cfg["port"],
        )
        with manager:
            manager.get_ppp_secrets()
    except Exception as e:
        log.error("Tower connection test failed: %s", e)
        return _err("connection_failed", f"فشل الاتصال بالراوتر: {e}", 400)
    return _ok({"message": "تم الاتصال بالراوتر بنجاح"})


# ── DEVICES (FCM registration) ───────────────────────────────

@mobile_bp.route("/devices", methods=["POST"])
@mobile_login_required
def mobile_devices_register():
    """POST /devices — register this device's FCM push token for the user."""
    data = request.get_json() or {}
    token = str(data.get("token", "")).strip()
    if not token:
        return _err("invalid_input", "رمز الجهاز مطلوب", 400)
    device_id = db.register_device(
        g.mobile_user["id"], token, platform=str(data.get("platform", "")).strip()
    )
    return _ok({"device_id": device_id})


# ── NETWORK TOOLS ────────────────────────────────────────────

@mobile_bp.route("/network/ping", methods=["POST"])
@mobile_login_required
def mobile_network_ping():
    """POST /network/ping — live ICMP ping (mirrors web api_network_ping)."""
    from network_tools.ping import ping_host

    data = request.get_json() or {}
    host = data.get("host", "").strip()
    try:
        count = int(data.get("count", 4))
    except (ValueError, TypeError):
        count = 4
    if not host:
        return _err("invalid_input", "عنوان IP أو مضيف مطلوب", 400)
    log.info("Mobile PING API host=%s count=%s", host, count)
    result = ping_host(host, count=count)
    return _ok(result)


@mobile_bp.route("/signal-board")
@mobile_login_required
def mobile_signal_board():
    """GET /signal-board — cached signals joined with customer names."""
    cache_rows = db.list_signal_cache()
    by_ip = {r["ip"]: dict(r) for r in cache_rows}
    items = []
    last_update = ""
    for c in db.list_active_customers():
        ip = (c.get("ip_address") or "").strip()
        row = by_ip.get(ip)
        if not row:
            continue
        dt = (c.get("device_type") or "").strip()
        row["type"] = "optical" if dt == "كيبل ضوئي" else "wireless"
        row["name"] = c["name"]
        if row.get("last_updated") and row["last_updated"] > last_update:
            last_update = row["last_updated"]
        items.append(row)
    known = {i["ip"] for i in items}
    for ip, row in by_ip.items():
        if ip in known:
            continue
        row["type"] = "wireless"
        row["name"] = ip
        items.append(row)
    return _ok({"items": items, "last_update": last_update})


@mobile_bp.route("/network/links")
@mobile_login_required
def mobile_network_links_list():
    """GET /network/links — all network links (sectors & links)."""
    links = db.list_network_links()
    return _ok({
        "items": [
            {
                "id": l["id"], "name": l["name"], "ip": l["ip"] or "",
                "link_type": l["link_type"], "location": l["location"] or "",
                "notes": l["notes"] or "", "created_at": l["created_at"],
                "username": l["username"] or "", "password": l["password"] or "",
                "community": l["community"] or "public",
            }
            for l in links
        ]
    })


@mobile_bp.route("/network/links", methods=["POST"])
@mobile_login_required
def mobile_network_links_add():
    """POST /network/links — add a network link (mirrors web add)."""
    from network_tools.ping import validate_host

    data = request.get_json() or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return _err("invalid_input", "اسم الجهاز مطلوب", 400)
    link_type = str(data.get("link_type", "MikroTik")).strip()
    if link_type not in ("MikroTik", "Ubnt", "Mimosa"):
        link_type = "MikroTik"
    ip = str(data.get("ip", "")).strip()
    if ip and not validate_host(ip):
        return _err("invalid_input", "عنوان IP غير صالح", 400)
    link_id = db.add_network_link(
        name=name, ip=ip, link_type=link_type,
        location=data.get("location", "").strip(),
        notes=data.get("notes", "").strip(),
        username=data.get("username", "").strip(),
        password=data.get("password", "").strip(),
        community=(data.get("community", "public").strip() or "public"),
    )
    _audit("اضافة جهاز شبكة", "network_link", link_id, f"تم اضافة الجهاز {name}")

    pull_summary = {}
    if link_type == "MikroTik" and ip and data.get("username", "").strip():
        try:
            from billing_system.mikrotik_sync import sync_pull_router, sync_push_cloud
            pull_summary = sync_pull_router(
                host=ip,
                username=data.get("username", "").strip(),
                password=data.get("password", ""),
                port=int(data.get("port", 8728) or 8728),
            )
            if pull_summary.get("secrets", 0) > 0:
                try:
                    sync_push_cloud()
                except Exception as e:  # noqa: BLE001
                    log.warning("[Mobile AutoSync] Cloud push failed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.error("[Mobile AutoSync] Pull failed for %s: %s", ip, e)
            pull_summary = {"errors": [str(e)]}

    return _ok({"link_id": link_id, "auto_pull": pull_summary})


@mobile_bp.route("/network/links/<int:link_id>", methods=["PUT"])
@mobile_login_required
def mobile_network_links_edit(link_id):
    """PUT /network/links/{id} — edit a network link (mirrors web edit)."""
    from network_tools.ping import validate_host

    link = db.get_network_link(link_id)
    if not link:
        return _err("not_found", "الجهاز غير موجود", 404)
    data = request.get_json() or {}
    ip = str(data.get("ip", "")).strip()
    if ip and not validate_host(ip):
        return _err("invalid_input", "عنوان IP غير صالح", 400)
    db.update_network_link(
        link_id,
        name=str(data.get("name", link["name"])).strip(),
        ip=ip,
        link_type=str(data.get("link_type", link["link_type"])).strip(),
        location=data.get("location", "").strip(),
        notes=data.get("notes", "").strip(),
        username=data.get("username", "").strip(),
        password=data.get("password", "").strip(),
        community=(data.get("community", "public").strip() or "public"),
    )
    _audit("تعديل جهاز شبكة", "network_link", link_id, f"تم تعديل الجهاز {link['name']}")

    pull_summary = {}
    link_type = str(data.get("link_type", link["link_type"] or "")).strip()
    if link_type == "MikroTik" and ip and data.get("username", "").strip():
        try:
            from billing_system.mikrotik_sync import sync_pull_router, sync_push_cloud
            pull_summary = sync_pull_router(
                host=ip,
                username=data.get("username", "").strip(),
                password=data.get("password", ""),
                port=int(data.get("port", 8728) or 8728),
            )
            if pull_summary.get("secrets", 0) > 0:
                try:
                    sync_push_cloud()
                except Exception as e:  # noqa: BLE001
                    log.warning("[Mobile AutoSync] Cloud push failed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.error("[Mobile AutoSync] Pull failed for %s: %s", ip, e)
            pull_summary = {"errors": [str(e)]}

    return _ok({"auto_pull": pull_summary})


@mobile_bp.route("/network/links/<int:link_id>", methods=["DELETE"])
@mobile_admin_required
def mobile_network_links_delete(link_id):
    """DELETE /network/links/{id} — delete a network link (admin only)."""
    link = db.get_network_link(link_id)
    if not link:
        return _err("not_found", "الجهاز غير موجود", 404)
    db.delete_network_link(link_id)
    _audit("حذف جهاز شبكة", "network_link", link_id, f"تم حذف الجهاز {link['name']}")
    return _ok({"deleted": True})


@mobile_bp.route("/network/sync", methods=["POST"])
@mobile_login_required
def mobile_network_sync():
    """POST /network/sync — pull subscribers from every saved MikroTik sector.

    Mirrors the web api_network_sync: on Render (cloud) it only reflects the
    latest tower-synced data (the EXE pulls locally and pushes via /sync/push);
    on the local PC/tower it pulls every MikroTik network link directly.
    """
    # Cloud (Render): the tower's MikroTik lives inside the LAN and is not
    # reachable from here — pulling would just time out. The desktop EXE pulls
    # locally and pushes the snapshot via /api/sync/push, so on the phone this
    # button only reflects the latest tower-synced data (instant, no timeout).
    if IS_RENDER:
        return _ok({
            "cloud_mode": True,
            "total_secrets": 0,
            "message": "السحب يتم على جهاز البرج (الكمبيوتر) — هذه الشاشة تعرض آخر بيانات تمت مزامنتها من البرج",
        })

    from billing_system.mikrotik_sync import sync_pull_router, sync_push_cloud

    links = db.list_network_links()
    results = []
    total_secrets = 0
    errors = []
    for link in links:
        if (link.get("link_type") or "") != "MikroTik":
            continue
        ip = (link.get("ip") or "").strip()
        username = (link.get("username") or "").strip()
        if not ip or not username:
            continue
        try:
            pull = sync_pull_router(
                host=ip,
                username=username,
                password=link.get("password") or "",
                port=int(link.get("port") or 8728),
            )
            total_secrets += pull.get("secrets", 0)
            results.append({"name": link["name"], "ip": ip, **pull})
        except Exception as e:  # noqa: BLE001 — one failure must not block others
            log.error("[Mobile NetSync] %s (%s): %s", link.get("name"), ip, e)
            errors.append(f"{link.get('name', ip)}: {e}")
        break  # single primary MikroTik is the tower's PPPoE source

    if total_secrets > 0:
        try:
            sync_push_cloud()
        except Exception as e:  # noqa: BLE001
            log.warning("[Mobile NetSync] Cloud push failed: %s", e)

    _audit("سحب مشتركين من الميكروتيك", "network", None,
           f"تم سحب {total_secrets} مشترك من الأجهزة")
    return _ok({
        "links": results,
        "total_secrets": total_secrets,
        "errors": errors,
    })


# ── REPORTS ──────────────────────────────────────────────────

@mobile_bp.route("/report")
@mobile_login_required
def mobile_report():
    """GET /report?month&year — monthly finance summary (mirrors web report)."""
    now = _now_dt()
    month = _to_int(request.args.get("month"), now.month)
    year = _to_int(request.args.get("year"), now.year)

    expected = db.sum_invoice_amounts(month, year, "total_amount")
    collected = db.sum_invoice_amounts(month, year, "paid_amount")
    expenses_total = db.total_expenses(month, year)
    remaining = expected - collected
    net_profit = collected - expenses_total
    return _ok({
        "month": month,
        "year": year,
        "expected": expected,
        "collected": collected,
        "collected_today": db.total_payments_today(),
        "total_debt": db.total_unpaid_debt(),
        "unpaid_count": db.count_unpaid_invoices(month, year),
        "expenses_total": expenses_total,
        "remaining": remaining,
        "net_profit": net_profit,
        "expense_categories": db.expense_categories(month, year),
        "paid_count": db.count_paid_invoices(month, year),
        "total_invoices": db.count_invoices(month, year),
    })


# ── LOGIN RATE LIMIT (per IP, mirrors auth.py) ────────────────

_login_attempts = defaultdict(deque)
_RATE_WINDOW_SECONDS = LOGIN_RATE_LIMIT_WINDOW_MINUTES * 60


def _login_rate_limited():
    """Return True when the caller's IP exceeded its login attempt budget."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    now = time()
    window = _login_attempts[ip]
    while window and now - window[0] > _RATE_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= LOGIN_RATE_LIMIT_ATTEMPTS:
        return True
    window.append(now)
    return False


def _clear_login_rate_limit():
    """Reset the login rate-limit window for the caller's IP."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    _login_attempts[ip].clear()