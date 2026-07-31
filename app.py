"""
Al-Nathim SaaS Platform — Flask Application
===========================================
Multi-tenant ISP management platform.
Phase 1: Cleanup, Multi-tenant database, SuperAdmin panel, Secure auth.
"""

import io
import logging
from datetime import datetime, timedelta, date

from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session, g

from config import SECRET_KEY
from database import (
    init_db, seed_default_owner, db_session,
    ISPOwner, Customer, Invoice, Payment, Expense, Package, InvoiceExtra, MaintenanceTicket,
)
from auth import (
    auth_bp, login_manager, owner_required, superadmin_required,
    get_current_owner_id,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_PERMANENT"] = False

# Register blueprints
app.register_blueprint(auth_bp)
login_manager.init_app(app)

# Initialize DB on startup
init_db()
seed_default_owner()


# ──────────────────────────────────────────────
#  HELPERS & FORMATTERS
# ──────────────────────────────────────────────

DATETIME_DB = "%Y-%m-%d %H:%M"
ARABIC_DIGITS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")


def now_dt():
    """Current datetime with seconds/microseconds zeroed."""
    return datetime.now().replace(second=0, microsecond=0)


def fmt_dt(dt):
    """Format datetime for display: YYYY-MM-DD HH:MM AM/PM."""
    if dt is None:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.strptime(dt, DATETIME_DB)
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %I:%M %p")


def fmt_dt_db(dt):
    """Format datetime for DB storage."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    return dt.strftime(DATETIME_DB)


def to_arabic_num(s):
    """Convert Western digits to Arabic numerals."""
    return str(s).translate(ARABIC_DIGITS)


def fmt_num(n, style="AR"):
    """Format with thousands separators, optionally Arabic digits."""
    try:
        f = f"{int(round(float(n))):,}"
    except (ValueError, TypeError):
        f = "0"
    return to_arabic_num(f) if style == "AR" else f


def get_owner_config():
    """Fetch the logged-in owner's config dict."""
    db = db_session()
    try:
        owner = db.query(ISPOwner).get(get_current_owner_id())
        return owner.config or {}
    finally:
        db.close()


def get_owner():
    """Return the full ISPOwner object for current user."""
    db = db_session()
    try:
        return db.query(ISPOwner).get(get_current_owner_id())
    finally:
        db.close()


# ──────────────────────────────────────────────
#  JINJA2 FILTERS
# ──────────────────────────────────────────────

@app.template_filter("dinar")
def dinar_filter(value):
    n = getattr(g, "numeral_style", "AR")
    if value is None:
        return (to_arabic_num("0") if n == "AR" else "0") + " د.ع"
    try:
        return fmt_num(value, n) + " د.ع"
    except (ValueError, TypeError):
        return (to_arabic_num("0") if n == "AR" else "0") + " د.ع"


@app.template_filter("invoice_no")
def invoice_no_filter(value):
    n = getattr(g, "numeral_style", "AR")
    try:
        f = f"#INV-{int(value):04d}"
    except (ValueError, TypeError):
        f = "#INV-0"
    return to_arabic_num(f) if n == "AR" else f


@app.template_filter("numeral")
def numeral_filter(value):
    n = getattr(g, "numeral_style", "AR")
    if value is None:
        return to_arabic_num("0") if n == "AR" else "0"
    try:
        return fmt_num(int(round(float(value))), n)
    except (ValueError, TypeError):
        return to_arabic_num("0") if n == "AR" else "0"


@app.template_filter("datetime_display")
def datetime_display_filter(value):
    return fmt_dt(value)


@app.template_filter("package_en")
def package_en_filter(value):
    """Map Arabic package names to English for receipts."""
    mapping = {
        "ايكونومي": "Economy", "بلاس": "Plus", "ستاندرد": "Standard",
        "توربو": "Turbo", "مور": "More", "بزنس برو": "Business Pro",
    }
    if value is None:
        return "—"
    return mapping.get(str(value).strip(), str(value))


@app.context_processor
def inject_globals():
    """Inject into templates: now_str + numeral style."""
    try:
        cfg = get_owner_config()
        numeral = cfg.get("numeral_style", "AR")
    except Exception:
        numeral = "AR"
    g.numeral_style = numeral
    return {"now_str": fmt_dt_db(now_dt()), "numeral_style": numeral}


# ──────────────────────────────────────────────
#  ROUTES: PAGES (owner-protected)
# ──────────────────────────────────────────────

@app.route("/")
@owner_required
def dashboard():
    """Dashboard KPIs scoped to the logged-in owner."""
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func, and_, or_

        active_customers = db.query(Customer).filter(
            Customer.owner_id == oid,
            Customer.is_active == True,
            Customer.subscription_status == "active",
        ).count()

        total_packages = db.query(Customer).filter(
            Customer.owner_id == oid,
            Customer.is_active == True,
            Customer.package_name.isnot(None),
            Customer.package_name != "",
        ).count()

        now = now_dt()
        month, year = now.month, now.year

        expected_income = db.query(func.coalesce(func.sum(Invoice.total_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year
        ).scalar() or 0

        collected = db.query(func.coalesce(func.sum(Invoice.paid_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year
        ).scalar() or 0

        total_debt = db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.is_paid == False
        ).scalar() or 0

        alert_days = request.args.get("alert_days", 3, type=int)
        alert_date = (now + timedelta(days=alert_days)).strftime(DATETIME_DB)
        expiring = db.query(Customer).filter(
            Customer.owner_id == oid,
            Customer.is_active == True,
            Customer.subscription_status == "active",
            Customer.renewal_date.isnot(None),
            Customer.renewal_date <= alert_date,
            Customer.renewal_date >= fmt_dt_db(now),
        ).order_by(Customer.renewal_date.asc()).all()

        db.close()
        return render_template(
            "dashboard.html",
            active_customers=active_customers or 0,
            total_packages=total_packages or 0,
            expected_income=expected_income,
            collected=collected,
            total_debt=total_debt,
            expiring=expiring,
            alert_days=alert_days,
        )
    finally:
        db.close()


@app.route("/customers")
@owner_required
def customers_page():
    """List customers scoped to owner."""
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func
        search = request.args.get("search", "").strip()
        status_filter = request.args.get("status", "all")
        region_filter = request.args.get("region", "").strip()
        sort_by = request.args.get("sort", "created_at")
        sort_dir = request.args.get("dir", "desc")

        valid_sorts = ["name", "created_at", "subscription_date", "renewal_date", "subscription_status", "region", "package_name", "package_price"]
        if sort_by not in valid_sorts:
            sort_by = "created_at"
        if sort_dir not in ["asc", "desc"]:
            sort_dir = "desc"

        query = db.query(Customer).filter(Customer.owner_id == oid)

        if search:
            like = f"%{search}%"
            query = query.filter(
                Customer.name.like(like) |
                Customer.phone.like(like) |
                Customer.phone2.like(like)
            )
        if status_filter == "active":
            query = query.filter(Customer.is_active == True, Customer.subscription_status == "active")
        elif status_filter == "expired":
            query = query.filter(Customer.subscription_status == "expired")
        elif status_filter == "suspended":
            query = query.filter(Customer.subscription_status == "suspended")
        elif status_filter == "inactive":
            query = query.filter(Customer.is_active == False)

        if region_filter:
            query = query.filter(Customer.region == region_filter)

        col = getattr(Customer, sort_by, Customer.created_at)
        query = query.order_by(col.desc() if sort_dir == "desc" else col.asc())
        customers = query.all()

        customer_list = []
        for c in customers:
            total_debt = db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0)).filter(
                Invoice.owner_id == oid,
                Invoice.customer_id == c.id,
                Invoice.is_paid == False,
            ).scalar() or 0
            customer_list.append({
                "id": c.id, "name": c.name, "phone": c.phone, "phone2": c.phone2,
                "address": c.address, "region": c.region, "username": c.username,
                "ip_address": c.ip_address, "device_type": c.device_type,
                "package_name": c.package_name, "package_price": c.package_price,
                "renewal_date": c.renewal_date, "subscription_status": c.subscription_status,
                "is_active": c.is_active, "created_at": c.created_at,
                "subscription_date": c.subscription_date, "total_debt": total_debt,
            })

        stats = {
            "total": db.query(Customer).filter(Customer.owner_id == oid).count(),
            "active": db.query(Customer).filter(
                Customer.owner_id == oid, Customer.is_active == True,
                Customer.subscription_status == "active",
            ).count(),
            "expired": db.query(Customer).filter(
                Customer.owner_id == oid, Customer.subscription_status == "expired",
            ).count(),
            "suspended": db.query(Customer).filter(
                Customer.owner_id == oid, Customer.subscription_status == "suspended",
            ).count(),
        }

        regions_raw = db.query(Customer.region).filter(
            Customer.owner_id == oid,
            Customer.region.isnot(None),
            Customer.region != "",
        ).distinct().all()
        regions = [r[0] for r in regions_raw]

        total_debt_all = db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.is_paid == False
        ).scalar() or 0

        return render_template(
            "customers.html",
            customers=customer_list, stats=stats,
            total_debt_all=total_debt_all,
            search=search, status_filter=status_filter,
            region_filter=region_filter,
            sort_by=sort_by, sort_dir=sort_dir,
            regions=regions,
        )
    finally:
        db.close()


@app.route("/debts")
@owner_required
def debts_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func
        customers_debt = db.query(
            Customer.id, Customer.name, Customer.phone,
            Customer.region, Customer.package_name, Customer.package_price,
            func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0).label("total_debt"),
            func.count(Invoice.id).label("unpaid_count"),
        ).join(Invoice, Invoice.customer_id == Customer.id).filter(
            Customer.owner_id == oid,
            Invoice.owner_id == oid,
            Invoice.is_paid == False,
        ).group_by(Customer.id).having(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0) > 0
        ).order_by(
            func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0).desc()
        ).all()

        total_debt_all = sum(c.total_debt for c in customers_debt)
        return render_template("debts.html", customers=customers_debt, total_debt_all=total_debt_all)
    finally:
        db.close()


@app.route("/reminders")
@owner_required
def reminders_page():
    """Customers who are expired or have debts — for manual WhatsApp deep links."""
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func
        now_str = fmt_dt_db(now_dt())
        customers = db.query(Customer).filter(
            Customer.owner_id == oid, Customer.is_active == True,
        ).all()

        result = []
        for c in customers:
            total_debt = db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0)).filter(
                Invoice.owner_id == oid, Invoice.customer_id == c.id, Invoice.is_paid == False,
            ).scalar() or 0
            if total_debt > 0 or (c.renewal_date and str(c.renewal_date) < now_str):
                result.append({
                    "id": c.id, "name": c.name, "phone": c.phone,
                    "phone2": c.phone2, "whatsapp_phone": c.whatsapp_phone,
                    "package_name": c.package_name, "package_price": c.package_price,
                    "region": c.region, "subscription_status": c.subscription_status,
                    "renewal_date": c.renewal_date, "total_debt": total_debt,
                })

        total_debt_all = sum(r["total_debt"] for r in result)
        return render_template("reminders.html", customers=result, total_debt_all=total_debt_all)
    finally:
        db.close()


@app.route("/payments")
@owner_required
def payments_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func
        search = request.args.get("search", "").strip()
        method_filter = request.args.get("method", "").strip()

        query = db.query(Payment, Customer.name.label("customer_name")).join(
            Customer, Customer.id == Payment.customer_id
        ).filter(Payment.owner_id == oid)

        if search:
            like = f"%{search}%"
            query = query.filter(Customer.name.like(like))
        if method_filter and method_filter in ("نقدي", "تحويل", "بطاقة", "غير ذلك"):
            query = query.filter(Payment.payment_method == method_filter)

        payments = query.order_by(Payment.payment_date.desc(), Payment.id.desc()).limit(200).all()

        total_all = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.owner_id == oid
        ).scalar() or 0

        total_today = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
            Payment.owner_id == oid, Payment.payment_date == date.today()
        ).scalar() or 0

        return render_template(
            "payments.html", payments=payments, search=search, method_filter=method_filter,
            total_all=total_all, total_today=total_today,
        )
    finally:
        db.close()


@app.route("/billing")
@owner_required
def billing_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func
        now = now_dt()
        month = request.args.get("month", now.month, type=int)
        year = request.args.get("year", now.year, type=int)
        search_bill = request.args.get("search", "").strip()

        query = db.query(Invoice, Customer.name.label("customer_name")).join(
            Customer, Customer.id == Invoice.customer_id
        ).filter(Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year)

        if search_bill:
            query = query.filter(Customer.name.like(f"%{search_bill}%"))
        rows = query.order_by(Customer.name).all()

        inv_list = []
        for inv, cust_name in rows:
            extras = db.query(InvoiceExtra).filter(
                InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == inv.id
            ).all()
            inv_list.append({
                "id": inv.id, "customer_id": inv.customer_id, "customer_name": cust_name,
                "month": inv.month, "year": inv.year, "package_name": inv.package_name,
                "package_price": inv.package_price, "total_amount": inv.total_amount,
                "paid_amount": inv.paid_amount, "is_paid": inv.is_paid,
                "previous_debt": inv.previous_debt,
                "extras": [{"id": e.id, "item_name": e.item_name, "item_price": e.item_price} for e in extras],
                "extras_total": sum(e.item_price for e in extras),
            })

        total_all = sum(i["total_amount"] for i in inv_list)
        total_paid = sum(i["paid_amount"] for i in inv_list)

        return render_template(
            "billing.html", invoices=inv_list, month=month, year=year,
            total_all=total_all, total_paid=total_paid,
            total_remaining=total_all - total_paid,
        )
    finally:
        db.close()


@app.route("/expenses")
@owner_required
def expenses_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func, extract
        now = now_dt()
        month = request.args.get("month", now.month, type=int)
        year = request.args.get("year", now.year, type=int)
        search = request.args.get("search", "").strip()
        category_filter = request.args.get("category", "").strip()

        subscribers = db.query(Customer).filter(
            Customer.owner_id == oid, Customer.is_active == True,
        ).order_by(Customer.name).all()

        query = db.query(Expense).filter(
            Expense.owner_id == oid,
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        )
        if search:
            like = f"%{search}%"
            query = query.filter(Expense.description.like(like) | Expense.category.like(like))
        if category_filter:
            query = query.filter(Expense.category == category_filter)
        expenses = query.order_by(Expense.expense_date.desc(), Expense.id.desc()).all()

        total_expenses = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.owner_id == oid,
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        ).scalar() or 0

        categories_raw = db.query(
            Expense.category,
            func.coalesce(func.sum(Expense.amount), 0).label("total"),
        ).filter(
            Expense.owner_id == oid,
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        ).group_by(Expense.category).order_by(
            func.coalesce(func.sum(Expense.amount), 0).desc()
        ).all()

        return render_template(
            "expenses.html", expenses=expenses, month=month, year=year,
            total_expenses=total_expenses, categories=categories_raw,
            search=search, category_filter=category_filter,
            subscribers=subscribers,
        )
    finally:
        db.close()


@app.route("/tickets")
@owner_required
def tickets_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        status_filter = request.args.get("status", "all")
        search = request.args.get("search", "").strip()

        query = db.query(
            MaintenanceTicket, Customer.name.label("customer_name"),
            Customer.phone.label("customer_phone"),
        ).join(Customer, Customer.id == MaintenanceTicket.customer_id).filter(
            MaintenanceTicket.owner_id == oid
        )

        if status_filter == "pending":
            query = query.filter(MaintenanceTicket.status == "pending")
        elif status_filter == "resolved":
            query = query.filter(MaintenanceTicket.status == "resolved")
        if search:
            like = f"%{search}%"
            query = query.filter(
                Customer.name.like(like) | MaintenanceTicket.issue_description.like(like)
            )
        rows = query.order_by(MaintenanceTicket.created_at.desc()).all()

        tickets = [{
            "id": t.id, "customer_id": t.customer_id, "customer_name": cname,
            "customer_phone": cphone, "issue_description": t.issue_description,
            "status": t.status, "created_at": t.created_at, "resolved_at": t.resolved_at,
        } for t, cname, cphone in rows]

        customers = db.query(Customer).filter(
            Customer.owner_id == oid, Customer.is_active == True,
        ).order_by(Customer.name).all()

        return render_template(
            "tickets.html", tickets=tickets, customers=customers,
            status_filter=status_filter, search=search,
        )
    finally:
        db.close()


@app.route("/packages")
@owner_required
def packages_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        packages = db.query(Package).filter(Package.owner_id == oid).order_by(Package.id).all()
        return render_template("packages.html", packages=packages)
    finally:
        db.close()


@app.route("/report")
@owner_required
def monthly_report():
    db = db_session()
    try:
        oid = get_current_owner_id()
        from sqlalchemy import func, extract
        now = now_dt()
        month = request.args.get("month", now.month, type=int)
        year = request.args.get("year", now.year, type=int)

        collected = db.query(func.coalesce(func.sum(Invoice.paid_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year
        ).scalar() or 0
        expected = db.query(func.coalesce(func.sum(Invoice.total_amount), 0)).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year
        ).scalar() or 0
        expenses_total = db.query(func.coalesce(func.sum(Expense.amount), 0)).filter(
            Expense.owner_id == oid,
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        ).scalar() or 0

        remaining = expected - collected
        net_profit = collected - expenses_total

        expense_categories = db.query(
            Expense.category, func.coalesce(func.sum(Expense.amount), 0).label("total"),
        ).filter(
            Expense.owner_id == oid,
            extract("month", Expense.expense_date) == month,
            extract("year", Expense.expense_date) == year,
        ).group_by(Expense.category).order_by(
            func.coalesce(func.sum(Expense.amount), 0).desc()
        ).all()

        paid_count = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year,
            Invoice.is_paid == True,
        ).count()
        total_invoices = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.month == month, Invoice.year == year,
        ).count()

        return render_template(
            "report.html", month=month, year=year,
            expected=expected, collected=collected,
            expenses_total=expenses_total, remaining=remaining, net_profit=net_profit,
            expense_categories=expense_categories,
            paid_count=paid_count, total_invoices=total_invoices,
        )
    finally:
        db.close()


@app.route("/settings")
@owner_required
def settings_page():
    db = db_session()
    try:
        oid = get_current_owner_id()
        owner = db.query(ISPOwner).get(oid)
        return render_template(
            "settings.html",
            gen_info=owner,
            default_connection_type=(owner.config or {}).get("default_connection_type", "كيبل ضوئي"),
        )
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: CUSTOMERS
# ──────────────────────────────────────────────

@app.route("/api/customers/search")
@owner_required
def api_customers_search():
    q = request.args.get("q", "").strip()
    oid = get_current_owner_id()
    db = db_session()
    try:
        query = db.query(Customer).filter(Customer.owner_id == oid)
        if q:
            like = f"%{q}%"
            query = query.filter(
                Customer.name.like(like) | Customer.username.like(like) | Customer.phone.like(like)
            )
        customers = query.order_by(Customer.name).limit(50).all()
        return jsonify({"ok": True, "customers": [
            {"id": c.id, "name": c.name, "username": c.username, "phone": c.phone} for c in customers
        ]})
    finally:
        db.close()


@app.route("/api/customers/add", methods=["POST"])
@owner_required
def api_customer_add():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "الاسم مطلوب"}), 400

    db = db_session()
    try:
        from dateutil.relativedelta import relativedelta
        existing = db.query(Customer).filter(Customer.owner_id == oid, Customer.name == name).first()
        if existing:
            return jsonify({"ok": False, "error": "يوجد مشترك بنفس الاسم"}), 400

        try:
            int_price = int(float(data.get("package_price", 0)))
        except (ValueError, TypeError):
            int_price = 0
        months = int(data.get("duration_months", 1) or 1)
        if months <= 0:
            months = 1

        now = now_dt()
        customer = Customer(
            owner_id=oid,
            name=name,
            phone=data.get("phone", "").strip(),
            phone2=data.get("phone2", "").strip(),
            whatsapp_phone=data.get("whatsapp_phone", "").strip(),
            address=data.get("address", "").strip(),
            region=data.get("region", "").strip(),
            notes=data.get("notes", "").strip(),
            username=data.get("username", "").strip(),
            password=data.get("password", "").strip(),
            ip_address=data.get("ip_address", "").strip(),
            device_type=data.get("device_type", "").strip(),
            package_name=data.get("package_name", "").strip(),
            package_price=int_price,
            subscription_date=now,
            renewal_date=now + relativedelta(months=months),
            subscription_status="active",
            is_active=True,
        )
        db.add(customer)
        db.flush()
        db.add(Invoice(
            owner_id=oid, customer_id=customer.id,
            month=now.month, year=now.year,
            package_name=customer.package_name, package_price=int_price,
            total_amount=int_price * months, paid_amount=0, is_paid=False,
        ))
        db.commit()
        return jsonify({"ok": True, "customer_id": customer.id})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": f"خطأ: {e}"}), 500
    finally:
        db.close()


@app.route("/api/customers/edit/<int:customer_id>", methods=["POST"])
@owner_required
def api_customer_edit(customer_id):
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404

        customer.name = data.get("name", customer.name).strip()
        customer.phone = data.get("phone", "").strip()
        customer.phone2 = data.get("phone2", "").strip()
        customer.whatsapp_phone = data.get("whatsapp_phone", "").strip()
        customer.address = data.get("address", "").strip()
        customer.region = data.get("region", "").strip()
        customer.notes = data.get("notes", "").strip()
        customer.username = data.get("username", "").strip()
        customer.password = data.get("password", "").strip()
        customer.ip_address = data.get("ip_address", "").strip()
        customer.device_type = data.get("device_type", "").strip()
        customer.package_name = data.get("package_name", "").strip()
        try:
            customer.package_price = int(float(data.get("package_price", 0)))
        except (ValueError, TypeError):
            customer.package_price = 0
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/customers/toggle/<int:customer_id>", methods=["POST"])
@owner_required
def api_customer_toggle(customer_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404
        customer.is_active = not customer.is_active
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/customers/status/<int:customer_id>", methods=["POST"])
@owner_required
def api_customer_status(customer_id):
    data = request.get_json() or {}
    new_status = data.get("status", "active")
    if new_status not in ("active", "expired", "suspended"):
        return jsonify({"ok": False, "error": "حالة غير صالحة"}), 400
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404
        customer.subscription_status = new_status
        customer.is_active = new_status not in ("expired", "suspended")
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/customers/renew/<int:customer_id>", methods=["POST"])
@owner_required
def api_customer_renew(customer_id):
    """Renew subscription by N months, appending to current expiry if in future."""
    data = request.get_json() or {}
    oid = get_current_owner_id()
    try:
        months = int(data.get("months", 1))
        if months <= 0:
            months = 1
    except (ValueError, TypeError):
        months = 1

    from dateutil.relativedelta import relativedelta
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404

        now = now_dt()
        base = customer.renewal_date if customer.renewal_date else now
        if base < now:
            base = now

        new_renewal = base + relativedelta(months=months)
        customer.renewal_date = new_renewal
        customer.subscription_status = "active"
        customer.is_active = True

        renew_total = (customer.package_price or 0) * months
        db.add(Invoice(
            owner_id=oid, customer_id=customer.id,
            month=now.month, year=now.year,
            package_name=customer.package_name, package_price=customer.package_price,
            total_amount=renew_total, paid_amount=0, is_paid=False,
        ))
        db.commit()
        return jsonify({"ok": True, "renewal_date": fmt_dt_db(new_renewal), "invoices_generated": 1})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/customers/delete/<int:customer_id>", methods=["POST"])
@owner_required
def api_customer_delete(customer_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404
        db.delete(customer)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": f"فشل الحذف: {e}"}), 500
    finally:
        db.close()


@app.route("/api/customers/history/<int:customer_id>")
@owner_required
def api_customer_history(customer_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404

        invoices = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.customer_id == customer_id
        ).order_by(Invoice.year.desc(), Invoice.month.desc()).all()
        payments = db.query(Payment).filter(
            Payment.owner_id == oid, Payment.customer_id == customer_id
        ).order_by(Payment.payment_date.desc()).all()

        inv_list = []
        for inv in invoices:
            extras = db.query(InvoiceExtra).filter(
                InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == inv.id
            ).all()
            inv_list.append({
                "id": inv.id, "month": inv.month, "year": inv.year,
                "package_name": inv.package_name, "package_price": inv.package_price,
                "total_amount": inv.total_amount, "paid_amount": inv.paid_amount,
                "is_paid": inv.is_paid,
                "extras": [{"id": e.id, "item_name": e.item_name, "item_price": e.item_price} for e in extras],
            })

        pay_list = [{
            "id": p.id, "amount": p.amount, "payment_date": p.payment_date,
            "payment_method": p.payment_method, "notes": p.notes,
        } for p in payments]

        return jsonify({"ok": True, "invoices": inv_list, "payments": pay_list})
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: PAYMENTS
# ──────────────────────────────────────────────

@app.route("/api/payment/quick-pay/<int:customer_id>", methods=["POST"])
@owner_required
def api_quick_pay(customer_id):
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404

        now = now_dt()
        month, year = now.month, now.year

        invoice = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.customer_id == customer_id,
            Invoice.month == month, Invoice.year == year,
        ).first()

        if not invoice:
            total = customer.package_price or 0
            invoice = Invoice(
                owner_id=oid, customer_id=customer_id, month=month, year=year,
                package_name=customer.package_name, package_price=customer.package_price,
                total_amount=total, paid_amount=0, is_paid=False,
            )
            db.add(invoice)
            db.flush()

        remaining = (invoice.total_amount or 0) - (invoice.paid_amount or 0)
        if remaining <= 0:
            return jsonify({"ok": False, "error": "الفاتورة مدفوعة بالكامل"}), 400

        try:
            pay_amount = float(data.get("amount")) if data.get("amount") else remaining
        except (ValueError, TypeError):
            pay_amount = remaining
        if pay_amount > remaining:
            pay_amount = remaining
        if pay_amount <= 0:
            return jsonify({"ok": False, "error": "المبلغ غير صالح"}), 400

        new_paid = (invoice.paid_amount or 0) + pay_amount
        invoice.paid_amount = new_paid
        invoice.is_paid = new_paid >= invoice.total_amount

        pay_date_str = data.get("payment_date", now.strftime("%Y-%m-%d"))
        try:
            pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
        except ValueError:
            pay_date = date.today()

        db.add(Payment(
            owner_id=oid, invoice_id=invoice.id, customer_id=customer_id,
            amount=pay_amount, payment_date=pay_date,
            payment_method=data.get("payment_method", "نقدي"),
            notes=data.get("notes", "").strip(),
        ))
        db.commit()
        return jsonify({
            "ok": True, "invoice_id": invoice.id, "amount": pay_amount,
            "remaining": (invoice.total_amount or 0) - new_paid,
        })
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/payment/make", methods=["POST"])
@owner_required
def api_make_payment():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    invoice_id = data.get("invoice_id")

    try:
        amount = float(data.get("amount", 0))
    except (ValueError, TypeError):
        amount = 0
    if not invoice_id or amount <= 0:
        return jsonify({"ok": False, "error": "البيانات غير صالحة"}), 400

    db = db_session()
    try:
        invoice = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.owner_id == oid).first()
        if not invoice:
            return jsonify({"ok": False, "error": "الفاتورة غير موجودة"}), 404

        remaining = (invoice.total_amount or 0) - (invoice.paid_amount or 0)
        if amount > remaining:
            amount = remaining

        new_paid = (invoice.paid_amount or 0) + amount
        invoice.paid_amount = new_paid
        invoice.is_paid = new_paid >= invoice.total_amount

        pay_date_str = data.get("payment_date", now_dt().strftime("%Y-%m-%d"))
        try:
            pay_date = datetime.strptime(pay_date_str, "%Y-%m-%d").date()
        except ValueError:
            pay_date = date.today()

        db.add(Payment(
            owner_id=oid, invoice_id=invoice.id, customer_id=invoice.customer_id,
            amount=amount, payment_date=pay_date,
            payment_method=data.get("payment_method", "نقدي"),
            notes=data.get("notes", "").strip(),
        ))
        db.commit()
        return jsonify({
            "ok": True, "paid_amount": new_paid,
            "remaining": (invoice.total_amount or 0) - new_paid,
        })
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/payment/edit/<int:payment_id>", methods=["POST"])
@owner_required
def api_payment_edit(payment_id):
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        payment = db.query(Payment).filter(Payment.id == payment_id, Payment.owner_id == oid).first()
        if not payment:
            return jsonify({"ok": False, "error": "الدفعة غير موجودة"}), 404

        try:
            new_amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            new_amount = 0
        if new_amount <= 0:
            return jsonify({"ok": False, "error": "المبلغ غير صالح"}), 400

        invoice = db.query(Invoice).filter(Invoice.id == payment.invoice_id, Invoice.owner_id == oid).first()
        if not invoice:
            return jsonify({"ok": False, "error": "الفاتورة غير موجودة"}), 404

        old_amount = payment.amount
        new_paid = (invoice.paid_amount or 0) - old_amount + new_amount
        if new_paid > invoice.total_amount:
            new_paid = invoice.total_amount
        if new_paid < 0:
            new_paid = 0
        invoice.paid_amount = new_paid
        invoice.is_paid = new_paid >= invoice.total_amount

        payment.amount = new_amount
        try:
            payment.payment_date = datetime.strptime(data.get("payment_date", ""), "%Y-%m-%d").date()
        except ValueError:
            pass
        payment.payment_method = data.get("payment_method", payment.payment_method)
        payment.notes = data.get("notes", "").strip()
        db.commit()
        return jsonify({"ok": True, "remaining": (invoice.total_amount or 0) - new_paid})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/payment/delete/<int:payment_id>", methods=["POST"])
@owner_required
def api_payment_delete(payment_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        payment = db.query(Payment).filter(Payment.id == payment_id, Payment.owner_id == oid).first()
        if not payment:
            return jsonify({"ok": False, "error": "الدفعة غير موجودة"}), 404

        invoice = db.query(Invoice).filter(Invoice.id == payment.invoice_id, Invoice.owner_id == oid).first()
        if invoice:
            new_paid = (invoice.paid_amount or 0) - payment.amount
            if new_paid < 0:
                new_paid = 0
            invoice.paid_amount = new_paid
            invoice.is_paid = new_paid >= (invoice.total_amount or 0)

        db.delete(payment)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/payment/current-invoice/<int:customer_id>")
@owner_required
def api_current_invoice(customer_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        now = now_dt()
        invoice = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.customer_id == customer_id,
            Invoice.month == now.month, Invoice.year == now.year,
        ).first()
        if invoice:
            return jsonify({
                "ok": True,
                "invoice": {"id": invoice.id, "total_amount": invoice.total_amount, "paid_amount": invoice.paid_amount},
                "remaining": (invoice.total_amount or 0) - (invoice.paid_amount or 0),
                "paid": invoice.paid_amount, "total": invoice.total_amount,
            })
        return jsonify({"ok": True, "invoice": None, "remaining": 0, "paid": 0, "total": 0})
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: EXPENSES
# ──────────────────────────────────────────────

@app.route("/api/expenses/add", methods=["POST"])
@owner_required
def api_expense_add():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        try:
            amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            amount = 0
        if amount <= 0:
            return jsonify({"ok": False, "error": "المبلغ يجب أن يكون أكبر من صفر"}), 400
        category = data.get("category", "أخرى").strip()
        if not category:
            return jsonify({"ok": False, "error": "التصنيف مطلوب"}), 400

        try:
            expense_date = datetime.strptime(data.get("expense_date", ""), "%Y-%m-%d").date()
        except ValueError:
            expense_date = date.today()

        subscriber_id = data.get("subscriber_id")
        if subscriber_id in ("", None, "null"):
            subscriber_id = None

        db.add(Expense(
            owner_id=oid, expense_date=expense_date, category=category,
            amount=amount, description=data.get("description", "").strip(),
            recipient_name=data.get("recipient_name", "").strip(),
            subscriber_id=int(subscriber_id) if subscriber_id else None,
        ))
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/expenses/edit/<int:expense_id>", methods=["POST"])
@owner_required
def api_expense_edit(expense_id):
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        ex = db.query(Expense).filter(Expense.id == expense_id, Expense.owner_id == oid).first()
        if not ex:
            return jsonify({"ok": False, "error": "المصروف غير موجود"}), 404

        try:
            amount = float(data.get("amount", 0))
        except (ValueError, TypeError):
            amount = 0
        if amount <= 0:
            return jsonify({"ok": False, "error": "المبلغ يجب أن يكون أكبر من صفر"}), 400

        ex.amount = amount
        ex.category = data.get("category", ex.category).strip()
        ex.description = data.get("description", "").strip()
        ex.recipient_name = data.get("recipient_name", "").strip()
        try:
            ex.expense_date = datetime.strptime(data.get("expense_date", ""), "%Y-%m-%d").date()
        except ValueError:
            pass
        sub_id = data.get("subscriber_id")
        ex.subscriber_id = int(sub_id) if sub_id not in ("", None, "null") else None
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/expenses/delete/<int:expense_id>", methods=["POST"])
@owner_required
def api_expense_delete(expense_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        ex = db.query(Expense).filter(Expense.id == expense_id, Expense.owner_id == oid).first()
        if not ex:
            return jsonify({"ok": False, "error": "المصروف غير موجود"}), 404
        db.delete(ex)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: PACKAGES
# ──────────────────────────────────────────────

@app.route("/api/packages/list")
@owner_required
def api_packages_list():
    oid = get_current_owner_id()
    db = db_session()
    try:
        packages = db.query(Package).filter(Package.owner_id == oid).order_by(Package.id).all()
        return jsonify({"ok": True, "packages": [
            {"id": p.id, "name": p.name, "price": p.price} for p in packages
        ]})
    finally:
        db.close()


@app.route("/api/packages/add", methods=["POST"])
@owner_required
def api_packages_add():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    name = data.get("name", "").strip()
    try:
        price = int(float(data.get("price", 0)))
    except (ValueError, TypeError):
        price = 0
    if not name or price <= 0:
        return jsonify({"ok": False, "error": "البيانات غير صالحة"}), 400
    db = db_session()
    try:
        existing = db.query(Package).filter(Package.owner_id == oid, Package.name == name).first()
        if existing:
            return jsonify({"ok": False, "error": "يوجد باقة بنفس الاسم"}), 400
        db.add(Package(owner_id=oid, name=name, price=price))
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/packages/edit/<int:package_id>", methods=["POST"])
@owner_required
def api_packages_edit(package_id):
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        pkg = db.query(Package).filter(Package.id == package_id, Package.owner_id == oid).first()
        if not pkg:
            return jsonify({"ok": False, "error": "الباقة غير موجودة"}), 404
        pkg.name = data.get("name", pkg.name).strip()
        try:
            pkg.price = int(float(data.get("price", 0)))
        except (ValueError, TypeError):
            pkg.price = 0
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/packages/delete/<int:package_id>", methods=["POST"])
@owner_required
def api_packages_delete(package_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        pkg = db.query(Package).filter(Package.id == package_id, Package.owner_id == oid).first()
        if not pkg:
            return jsonify({"ok": False, "error": "الباقة غير موجودة"}), 404
        db.delete(pkg)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: INVOICING & TICKETS
# ──────────────────────────────────────────────

@app.route("/api/billing/generate", methods=["POST"])
@owner_required
def api_billing_generate():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    now = now_dt()
    month = int(data.get("month", now.month))
    year = int(data.get("year", now.year))
    db = db_session()
    try:
        customers = db.query(Customer).filter(
            Customer.owner_id == oid, Customer.is_active == True,
            Customer.subscription_status == "active",
        ).all()
        generated = skipped = 0
        for c in customers:
            exists = db.query(Invoice).filter(
                Invoice.owner_id == oid, Invoice.customer_id == c.id,
                Invoice.month == month, Invoice.year == year,
            ).first()
            if exists:
                skipped += 1
                continue
            db.add(Invoice(
                owner_id=oid, customer_id=c.id, month=month, year=year,
                package_name=c.package_name, package_price=c.package_price,
                total_amount=c.package_price or 0, paid_amount=0, is_paid=False,
            ))
            generated += 1
        db.commit()
        return jsonify({"ok": True, "generated": generated, "skipped": skipped})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/invoice/extras/add", methods=["POST"])
@owner_required
def api_invoice_extra_add():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        from sqlalchemy import func
        invoice_id = data.get("invoice_id")
        item_name = data.get("item_name", "").strip()
        try:
            item_price = int(float(data.get("item_price", 0)))
        except (ValueError, TypeError):
            item_price = 0
        if not invoice_id or not item_name or item_price <= 0:
            return jsonify({"ok": False, "error": "البيانات غير صالحة"}), 400

        inv = db.query(Invoice).filter(Invoice.id == invoice_id, Invoice.owner_id == oid).first()
        if not inv:
            return jsonify({"ok": False, "error": "الفاتورة غير موجودة"}), 404

        db.add(InvoiceExtra(owner_id=oid, invoice_id=invoice_id, item_name=item_name, item_price=item_price))
        extras_total = db.query(func.coalesce(func.sum(InvoiceExtra.item_price), 0)).filter(
            InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == invoice_id
        ).scalar() or 0
        inv.total_amount = (inv.package_price or 0) + extras_total
        db.commit()
        return jsonify({"ok": True, "new_total": inv.total_amount})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/invoice/extras/delete/<int:extra_id>", methods=["POST"])
@owner_required
def api_invoice_extra_delete(extra_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        from sqlalchemy import func
        extra = db.query(InvoiceExtra).filter(InvoiceExtra.id == extra_id, InvoiceExtra.owner_id == oid).first()
        if not extra:
            return jsonify({"ok": False, "error": "العنصر غير موجود"}), 404
        inv = db.query(Invoice).filter(Invoice.id == extra.invoice_id, Invoice.owner_id == oid).first()
        db.delete(extra)
        if inv:
            extras_total = db.query(func.coalesce(func.sum(InvoiceExtra.item_price), 0)).filter(
                InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == inv.id
            ).scalar() or 0
            inv.total_amount = (inv.package_price or 0) + extras_total
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/tickets/add", methods=["POST"])
@owner_required
def api_ticket_add():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        customer_id = data.get("customer_id")
        issue = data.get("issue_description", "").strip()
        if not customer_id:
            return jsonify({"ok": False, "error": "يرجى اختيار المشترك"}), 400
        if not issue:
            return jsonify({"ok": False, "error": "يرجى كتابة وصف المشكلة"}), 400

        customer = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_id == oid).first()
        if not customer:
            return jsonify({"ok": False, "error": "المشترك غير موجود"}), 404

        db.add(MaintenanceTicket(
            owner_id=oid, customer_id=customer_id,
            issue_description=issue, status="pending",
        ))
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/tickets/resolve/<int:ticket_id>", methods=["POST"])
@owner_required
def api_ticket_resolve(ticket_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        ticket = db.query(MaintenanceTicket).filter(
            MaintenanceTicket.id == ticket_id, MaintenanceTicket.owner_id == oid
        ).first()
        if not ticket:
            return jsonify({"ok": False, "error": "طلب الصيانة غير موجود"}), 404
        if ticket.status == "pending":
            ticket.status = "resolved"
            ticket.resolved_at = now_dt()
        else:
            ticket.status = "pending"
            ticket.resolved_at = None
        db.commit()
        return jsonify({"ok": True, "new_status": ticket.status})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/tickets/delete/<int:ticket_id>", methods=["POST"])
@owner_required
def api_ticket_delete(ticket_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        ticket = db.query(MaintenanceTicket).filter(
            MaintenanceTicket.id == ticket_id, MaintenanceTicket.owner_id == oid
        ).first()
        if not ticket:
            return jsonify({"ok": False, "error": "طلب الصيانة غير موجود"}), 404
        db.delete(ticket)
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/billing/rollover", methods=["POST"])
@owner_required
def api_debt_rollover():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    now = now_dt()
    target_month = int(data.get("month", now.month))
    target_year = int(data.get("year", now.year))
    db = db_session()
    try:
        from sqlalchemy import func, or_, and_
        inv_count = db.query(Invoice).filter(
            Invoice.owner_id == oid, Invoice.month == target_month,
            Invoice.year == target_year,
        ).count()
        if inv_count == 0:
            return jsonify({
                "ok": False, "error": "لا توجد فواتير للشهر المحدد.",
                "code": "NO_INVOICES",
            }), 400

        customers = db.query(Customer).filter(
            Customer.owner_id == oid, Customer.is_active == True,
            Customer.subscription_status == "active",
        ).all()

        rolled = 0
        for c in customers:
            prev_debt = db.query(func.coalesce(func.sum(Invoice.total_amount - Invoice.paid_amount), 0)).filter(
                Invoice.owner_id == oid, Invoice.customer_id == c.id,
                Invoice.is_paid == False,
                or_(Invoice.year < target_year,
                    and_(Invoice.year == target_year, Invoice.month <= target_month)),
            ).scalar() or 0
            if prev_debt <= 0:
                continue

            inv = db.query(Invoice).filter(
                Invoice.owner_id == oid, Invoice.customer_id == c.id,
                Invoice.month == target_month, Invoice.year == target_year,
            ).first()
            if inv:
                inv.previous_debt = prev_debt
            else:
                db.add(Invoice(
                    owner_id=oid, customer_id=c.id, month=target_month, year=target_year,
                    package_name=c.package_name, package_price=c.package_price,
                    total_amount=c.package_price or 0, paid_amount=0, is_paid=False,
                    previous_debt=prev_debt,
                ))
            rolled += 1

        db.commit()
        return jsonify({"ok": True, "rolled": rolled})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────
#  API: SETTINGS (owner-scoped)
# ──────────────────────────────────────────────

@app.route("/api/settings/numeral-style", methods=["POST"])
@owner_required
def api_numeral_style():
    data = request.get_json() or {}
    style = data.get("numeral_style", "AR")
    if style not in ("AR", "EN"):
        return jsonify({"ok": False, "error": "قيمة غير صالحة"}), 400
    oid = get_current_owner_id()
    db = db_session()
    try:
        owner = db.query(ISPOwner).get(oid)
        cfg = owner.config or {}
        cfg["numeral_style"] = style
        owner.config = cfg
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/settings/default-connection", methods=["POST"])
@owner_required
def api_settings_default_connection():
    data = request.get_json() or {}
    conn_type = data.get("default_connection_type", "كيبل ضوئي")
    if conn_type not in ("كيبل ضوئي", "نانو"):
        return jsonify({"ok": False, "error": "نوع ربط غير صالح"}), 400
    oid = get_current_owner_id()
    db = db_session()
    try:
        owner = db.query(ISPOwner).get(oid)
        cfg = owner.config or {}
        cfg["default_connection_type"] = conn_type
        owner.config = cfg
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


@app.route("/api/generator/update", methods=["POST"])
@owner_required
def api_isp_info_update():
    data = request.get_json() or {}
    oid = get_current_owner_id()
    db = db_session()
    try:
        owner = db.query(ISPOwner).get(oid)
        owner.owner_name = data.get("owner_name", "").strip()
        owner.phone = data.get("phone", "").strip()
        owner.address = data.get("address", "").strip()
        owner.footer_note = data.get("footer_note", "").strip()
        db.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.rollback()
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        db.close()


# ──────────────────────────────────────────────
#  PRINT ROUTES
# ──────────────────────────────────────────────

@app.route("/print/receipt/<int:payment_id>")
@owner_required
def print_receipt(payment_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        row = db.query(Payment, Customer, Invoice).join(
            Invoice, Invoice.id == Payment.invoice_id
        ).join(Customer, Customer.id == Payment.customer_id).filter(
            Payment.id == payment_id, Payment.owner_id == oid
        ).first()
        if not row:
            return "الايصال غير موجود", 404
        pay, cust, inv = row
        owner = db.query(ISPOwner).get(oid)
        remaining = (inv.total_amount or 0) - (inv.paid_amount or 0)
        return render_template(
            "print_receipt.html", payment=pay, customer=cust, invoice=inv,
            gen_info=owner, remaining=remaining,
        )
    finally:
        db.close()


@app.route("/print/a4/receipt/<int:payment_id>")
@owner_required
def print_a4_receipt(payment_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        row = db.query(Payment, Customer, Invoice).join(
            Invoice, Invoice.id == Payment.invoice_id
        ).join(Customer, Customer.id == Payment.customer_id).filter(
            Payment.id == payment_id, Payment.owner_id == oid
        ).first()
        if not row:
            return "الايصال غير موجود", 404
        pay, cust, inv = row
        owner = db.query(ISPOwner).get(oid)
        remaining = (inv.total_amount or 0) - (inv.paid_amount or 0)
        return render_template(
            "print_a4_receipt.html", payment=pay, customer=cust, invoice=inv,
            gen_info=owner, remaining=remaining,
        )
    finally:
        db.close()


@app.route("/print/invoice/<int:invoice_id>")
@owner_required
def print_invoice(invoice_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        row = db.query(Invoice, Customer).join(
            Customer, Customer.id == Invoice.customer_id
        ).filter(Invoice.id == invoice_id, Invoice.owner_id == oid).first()
        if not row:
            return "الفاتورة غير موجودة", 404
        inv, cust = row
        payments = db.query(Payment).filter(
            Payment.owner_id == oid, Payment.invoice_id == invoice_id
        ).order_by(Payment.payment_date.desc()).all()
        extras = db.query(InvoiceExtra).filter(
            InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == invoice_id
        ).all()
        owner = db.query(ISPOwner).get(oid)
        remaining = (inv.total_amount or 0) - (inv.paid_amount or 0)
        return render_template(
            "print_invoice_unified.html", invoice=inv, customer=cust,
            payments=payments, extras=extras, gen_info=owner, remaining=remaining,
        )
    finally:
        db.close()


@app.route("/print/a4/invoice/<int:invoice_id>")
@owner_required
def print_a4_invoice(invoice_id):
    oid = get_current_owner_id()
    db = db_session()
    try:
        row = db.query(Invoice, Customer).join(
            Customer, Customer.id == Invoice.customer_id
        ).filter(Invoice.id == invoice_id, Invoice.owner_id == oid).first()
        if not row:
            return "الفاتورة غير موجودة", 404
        inv, cust = row
        payments = db.query(Payment).filter(
            Payment.owner_id == oid, Payment.invoice_id == invoice_id
        ).order_by(Payment.payment_date.desc()).all()
        extras = db.query(InvoiceExtra).filter(
            InvoiceExtra.owner_id == oid, InvoiceExtra.invoice_id == invoice_id
        ).all()
        owner = db.query(ISPOwner).get(oid)
        remaining = (inv.total_amount or 0) - (inv.paid_amount or 0)
        return render_template(
            "print_a4_invoice.html", invoice=inv, customer=cust,
            payments=payments, extras=extras, gen_info=owner, remaining=remaining,
        )
    finally:
        db.close()


# ──────────────────────────────────────────────
#  EXPORT
# ──────────────────────────────────────────────

@app.route("/api/export/excel")
@owner_required
def api_export_excel():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    oid = get_current_owner_id()
    db = db_session()
    try:
        customers = db.query(Customer).filter(Customer.owner_id == oid).order_by(Customer.name).all()
        invoices = db.query(Invoice, Customer.name.label("customer_name")).join(
            Customer, Customer.id == Invoice.customer_id
        ).filter(Invoice.owner_id == oid).order_by(Customer.name, Invoice.year, Invoice.month).all()
        payments = db.query(Payment, Customer.name.label("customer_name")).join(
            Customer, Customer.id == Payment.customer_id
        ).filter(Payment.owner_id == oid).order_by(Payment.payment_date.desc()).all()

        wb = Workbook()
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        header_fill = PatternFill(start_color="1a1a1a", end_color="1a1a1a", fill_type="solid")
        header_font = Font(name="Cairo", bold=True, color="FFFFFF", size=12)
        body_font = Font(name="Cairo", size=11)

        def write_sheet(ws, title, headers, rows):
            ws.title = title
            ws.views.sheetView[0].rightToLeft = True
            for col_idx, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col_idx, value=h)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = thin_border
            for row_idx, row_data in enumerate(rows, 2):
                for col_idx, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_idx, column=col_idx, value=val)
                    cell.font = body_font
                    cell.alignment = Alignment(horizontal="right", vertical="center")
                    cell.border = thin_border
            for col_cells in ws.columns:
                max_len = 0
                col_letter = col_cells[0].column_letter
                for cell in col_cells:
                    try:
                        cell_len = len(str(cell.value or ""))
                        if cell_len > max_len:
                            max_len = cell_len
                    except Exception:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

        status_map = {"active": "نشط", "inactive": "معطل", "suspended": "موقوف", "expired": "منتهي"}

        ws1 = wb.active
        write_sheet(ws1, "المشتركين",
            ["ID", "الاسم", "الهاتف", "الهاتف 2", "العنوان", "المنطقة", "الباقة",
             "سعر الباقة", "اسم المستخدم", "IP", "نوع الجهاز", "الحالة",
             "تاريخ الاشتراك", "تاريخ التجديد", "ملاحظات"],
            [[c.id, c.name, c.phone or "", c.phone2 or "", c.address or "", c.region or "",
              c.package_name or "", c.package_price or 0, c.username or "",
              c.ip_address or "", c.device_type or "",
              status_map.get(c.subscription_status, c.subscription_status),
              fmt_dt(c.subscription_date), fmt_dt(c.renewal_date), c.notes or ""]
             for c in customers])

        ws2 = wb.create_sheet()
        write_sheet(ws2, "الفواتير",
            ["ID", "المشترك", "الشهر", "السنة", "الباقة", "سعر الباقة",
             "الإجمالي", "المدفوع", "المتبقي", "الحالة"],
            [[inv.id, cname, inv.month, inv.year, inv.package_name or "",
              int(round(float(inv.package_price or 0))),
              int(round(float(inv.total_amount or 0))),
              int(round(float(inv.paid_amount or 0))),
              int(round(float((inv.total_amount or 0) - (inv.paid_amount or 0)))),
              "مسدد" if inv.is_paid else ("مدفوع جزئياً" if inv.paid_amount > 0 else "غير مسدد")]
             for inv, cname in invoices])

        ws3 = wb.create_sheet()
        write_sheet(ws3, "المدفوعات",
            ["ID", "المشترك", "المبلغ", "التاريخ", "طريقة الدفع", "ملاحظات"],
            [[p.id, cname, int(round(float(p.amount))), p.payment_date,
              p.payment_method, p.notes or ""] for p, cname in payments])

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        today = now_dt().strftime("%Y-%m-%d")
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"isp_export_{today}.xlsx",
        )
    finally:
        db.close()


@app.route("/api/export/payments/excel")
@owner_required
def api_export_payments_excel():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    oid = get_current_owner_id()
    db = db_session()
    try:
        payments = db.query(Payment, Customer.name.label("customer_name")).join(
            Customer, Customer.id == Payment.customer_id
        ).filter(Payment.owner_id == oid).order_by(
            Payment.payment_date.desc(), Payment.id.desc()
        ).all()

        wb = Workbook()
        ws = wb.active
        ws.title = "المدفوعات"
        ws.views.sheetView[0].rightToLeft = True
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )
        header_fill = PatternFill(start_color="1a1a1a", end_color="1a1a1a", fill_type="solid")
        header_font = Font(name="Cairo", bold=True, color="FFFFFF", size=12)
        body_font = Font(name="Cairo", size=11)

        headers = ["ID", "المشترك", "المبلغ", "التاريخ", "الوقت", "طريقة الدفع", "ملاحظات"]
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col_idx, value=h)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        for row_idx, (p, cname) in enumerate(payments, 2):
            created_time = ""
            if p.created_at:
                try:
                    created_time = p.created_at.strftime("%I:%M %p")
                except Exception:
                    created_time = str(p.created_at)
            row_data = [p.id, cname, int(round(float(p.amount))), p.payment_date,
                        created_time, p.payment_method, p.notes or ""]
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = body_font
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.border = thin_border

        for col_cells in ws.columns:
            max_len = 0
            col_letter = col_cells[0].column_letter
            for cell in col_cells:
                try:
                    cell_len = len(str(cell.value or ""))
                    if cell_len > max_len:
                        max_len = cell_len
                except Exception:
                    pass
            ws.column_dimensions[col_letter].width = min(max_len + 4, 40)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(
            output,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"payments_export_{now_dt().strftime('%Y-%m-%d')}.xlsx",
        )
    finally:
        db.close()


@app.route("/api/backup")
@owner_required
def api_backup():
    """Backup — SQLite returns file, Postgres yields informational message."""
    from config import IS_POSTGRES, LOCAL_DB_PATH
    import os
    if IS_POSTGRES:
        return jsonify({
            "ok": False,
            "error": "النسخ الاحتياطي متاح تلقائياً على منصة Render (PostgreSQL daily backups).",
        })
    if not os.path.exists(LOCAL_DB_PATH):
        return jsonify({"ok": False, "error": "قاعدة البيانات غير موجودة"}), 404
    return send_file(
        LOCAL_DB_PATH,
        as_attachment=True,
        download_name=f"isp_backup_{now_dt().strftime('%Y-%m-%d')}.db",
        mimetype="application/x-sqlite3",
    )


# ──────────────────────────────────────────────
#  ENTRYPOINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)