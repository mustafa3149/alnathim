"""
Authentication & Authorization for Al-Nathim SaaS
=================================================
- ISP Owners: register → pending → approved by SuperAdmin → login
- SuperAdmin: separate login on /superadmin/login
- Flask-Login for owner sessions; session flag for superadmin
"""

from datetime import date
from functools import wraps
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

from database import db_session, ISPOwner, SuperAdmin

# ── Flask-Login setup ───────────────────────────────────────

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "يرجى تسجيل الدخول أولاً"
login_manager.login_message_category = "error"


class OwnerUser(UserMixin):
    """Wrapper class for Flask-Login around ISPOwner."""

    def __init__(self, owner):
        self.id = str(owner.id)
        self.owner = owner


@login_manager.user_loader
def load_user(user_id):
    """Load an ISP Owner by ID from the database."""
    db = db_session()
    try:
        owner = db.query(ISPOwner).filter(ISPOwner.id == int(user_id)).first()
        return OwnerUser(owner) if owner else None
    finally:
        db.close()


# ── Blueprint ───────────────────────────────────────────────

auth_bp = Blueprint("auth", __name__)


# ── Decorators ──────────────────────────────────────────────

def owner_required(fn):
    """Require an authenticated ISP Owner whose account is active & subscribed."""
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        owner = current_user.owner
        if owner.account_status != "active":
            logout_user()
            flash("حسابك غير مفعل. يرجى انتظار موافقة الإدارة.", "error")
            return redirect(url_for("auth.login"))
        # Check subscription expiry
        if owner.subscription_end_date and owner.subscription_end_date < date.today():
            logout_user()
            flash("انتهى اشتراكك. يرجى التواصل مع الإدارة لتجديده.", "error")
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)
    return wrapper


def superadmin_required(fn):
    """Require an authenticated SuperAdmin session."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("superadmin_id"):
            return redirect(url_for("auth.superadmin_login"))
        return fn(*args, **kwargs)
    return wrapper


# ── ISP Owner: Register ─────────────────────────────────────

@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Create a new ISP owner account (status defaults to 'pending')."""
    if request.method == "GET":
        return render_template("register.html")

    data = request.get_json() or {}
    full_name = data.get("full_name", "").strip()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not full_name or not username or not password:
        return jsonify({"ok": False, "error": "جميع الحقول مطلوبة"}), 400

    db = db_session()
    try:
        existing = db.query(ISPOwner).filter_by(username=username).first()
        if existing:
            return jsonify({"ok": False, "error": "اسم المستخدم مستخدم مسبقاً"}), 400

        owner = ISPOwner(
            full_name=full_name,
            username=username,
            account_status="pending",
        )
        owner.set_password(password)
        db.add(owner)
        db.commit()
        return jsonify({
            "ok": True,
            "message": "تم إنشاء الحساب بنجاح! بانتظار موافقة الإدارة لتفعيله.",
        })
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": f"خطأ في التسجيل: {e}"}), 500
    finally:
        db.close()


# ── ISP Owner: Login / Logout ───────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate an ISP Owner. Only 'active' accounts can log in."""
    if request.method == "GET":
        return render_template("login.html")

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "أدخل اسم المستخدم وكلمة المرور"}), 400

    db = db_session()
    try:
        owner = db.query(ISPOwner).filter_by(username=username).first()
        if not owner or not owner.check_password(password):
            return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 400

        if owner.account_status == "pending":
            return jsonify({
                "ok": False,
                "error": "حسابك بانتظار موافقة الإدارة. يرجى المحاولة لاحقاً.",
            }), 403

        if owner.account_status == "suspended":
            return jsonify({
                "ok": False,
                "error": "تم إيقاف حسابك. يرجى التواصل مع الإدارة.",
            }), 403

        if owner.subscription_end_date and owner.subscription_end_date < date.today():
            return jsonify({
                "ok": False,
                "error": "انتهى اشتراكك. يرجى التواصل مع الإدارة لتجديده.",
            }), 403

        login_user(OwnerUser(owner))
        return jsonify({"ok": True, "message": "تم تسجيل الدخول بنجاح"})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": f"خطأ: {e}"}), 500
    finally:
        db.close()


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log out the current ISP Owner."""
    logout_user()
    return jsonify({"ok": True, "message": "تم تسجيل الخروج"})


# ── SuperAdmin: Login / Logout ──────────────────────────────

@auth_bp.route("/superadmin/login", methods=["GET", "POST"])
def superadmin_login():
    """Separate login page for the platform SuperAdmin."""
    if session.get("superadmin_id"):
        return redirect(url_for("auth.superadmin_panel"))

    if request.method == "GET":
        return render_template("superadmin_login.html")

    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"ok": False, "error": "أدخل اسم المستخدم وكلمة المرور"}), 400

    db = db_session()
    try:
        admin = db.query(SuperAdmin).filter_by(username=username).first()
        if not admin or not admin.check_password(password):
            return jsonify({"ok": False, "error": "بيانات الدخول غير صحيحة"}), 400

        session.permanent = True
        session["superadmin_id"] = admin.id
        session["superadmin_username"] = admin.username
        return jsonify({"ok": True, "message": "تم تسجيل دخول المدير بنجاح"})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": f"خطأ: {e}"}), 500
    finally:
        db.close()


@auth_bp.route("/superadmin/logout", methods=["POST"])
def superadmin_logout():
    """Log out the SuperAdmin."""
    session.pop("superadmin_id", None)
    session.pop("superadmin_username", None)
    return jsonify({"ok": True, "message": "تم تسجيل خروج المدير"})


# ── SuperAdmin: Panel ───────────────────────────────────────

@auth_bp.route("/superadmin")
@superadmin_required
def superadmin_panel():
    """List all registered ISP owners."""
    db = db_session()
    try:
        owners = db.query(ISPOwner).order_by(ISPOwner.created_at.desc()).all()
        return render_template("superadmin.html", owners=owners)
    finally:
        db.close()


# ── SuperAdmin: Owner management actions ────────────────────

@auth_bp.route("/superadmin/owners/<int:owner_id>/approve", methods=["POST"])
@superadmin_required
def superadmin_approve(owner_id):
    """Approve a pending ISP owner account."""
    db = db_session()
    try:
        owner = db.query(ISPOwner).filter_by(id=owner_id).first()
        if not owner:
            return jsonify({"ok": False, "error": "المالك غير موجود"}), 404

        owner.account_status = "active"
        db.commit()
        return jsonify({"ok": True, "message": f"تم تفعيل حساب {owner.username}"})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@auth_bp.route("/superadmin/owners/<int:owner_id>/suspend", methods=["POST"])
@superadmin_required
def superadmin_suspend(owner_id):
    """Suspend / ban an ISP owner account."""
    db = db_session()
    try:
        owner = db.query(ISPOwner).filter_by(id=owner_id).first()
        if not owner:
            return jsonify({"ok": False, "error": "المالك غير موجود"}), 404

        owner.account_status = "suspended"
        db.commit()
        return jsonify({"ok": True, "message": f"تم إيقاف حساب {owner.username}"})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@auth_bp.route("/superadmin/owners/<int:owner_id>/extend", methods=["POST"])
@superadmin_required
def superadmin_extend(owner_id):
    """Set / extend the subscription end date for an owner."""
    data = request.get_json() or {}
    end_date_str = data.get("subscription_end_date", "").strip()

    if not end_date_str:
        return jsonify({"ok": False, "error": "يرجى تحديد تاريخ الانتهاء"}), 400

    try:
        from datetime import datetime
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"ok": False, "error": "صيغة التاريخ غير صحيحة"}), 400

    db = db_session()
    try:
        owner = db.query(ISPOwner).filter_by(id=owner_id).first()
        if not owner:
            return jsonify({"ok": False, "error": "المالك غير موجود"}), 404

        owner.subscription_end_date = end_date
        db.commit()
        return jsonify({
            "ok": True,
            "message": f"تم تمديد اشتراك {owner.username} حتى {end_date_str}",
        })
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ── Helper: current owner_id shortcut ───────────────────────

def get_current_owner_id():
    """Return the owner_id of the logged-in ISP owner."""
    return current_user.owner.id