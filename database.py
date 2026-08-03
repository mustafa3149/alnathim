"""
Database Layer — Al-Nathim ISP Management System
================================================
Raw SQLite (mawlidati.db) with strict `?` parameterized queries.
No ORM, no string interpolation of values.

Tables (per .clinerules §8.2/§8.3):
    users, packages, customers, invoices, payments,
    expenses, invoice_extras, maintenance_tickets,
    settings, generator_info

Every connection is opened through get_db() and MUST be closed
by the caller (usually via `with get_db() as db:` or try/finally).
"""

import os
import sqlite3
from datetime import date, datetime

from werkzeug.security import generate_password_hash, check_password_hash

from config import LOCAL_DB_PATH, SUPERADMIN_USERNAME, SUPERADMIN_PASSWORD

# ── Defaults ────────────────────────────────────────────────

DEFAULT_PACKAGES = [
    # (name, speed, price IQD)  — .clinerules §8.3
    ("Economy",  "20 Mbps",  35000),
    ("Plus",     "40 Mbps",  45000),
    ("Standard", "60 Mbps",  50000),
    ("Turbo",    "80 Mbps",  65000),
    ("More",     "100 Mbps", 75000),
    ("Business", "150 Mbps", 100000),
]

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"

DEFAULT_SETTINGS = {
    "numeral_style": "AR",          # AR = أرقام عربية, EN = English digits
    "default_connection_type": "كيبل ضوئي",
}

DEFAULT_GENERATOR_INFO = {
    "owner_name": "",
    "company_name": "الناظم",
    "phone": "",
    "address": "",
    "footer_note": "شكراً لتعاملكم معنا",
}

DATETIME_DB = "%Y-%m-%d %H:%M"


# ── Connection ──────────────────────────────────────────────

def get_db():
    """Open a SQLite connection to mawlidati.db.

    Returns:
        sqlite3.Connection with Row factory and foreign keys enabled.
    """
    os.makedirs(os.path.dirname(LOCAL_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(LOCAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _fetchall(sql, params=()):
    """Run a SELECT and return list of sqlite3.Row."""
    db = get_db()
    try:
        return db.execute(sql, params).fetchall()
    finally:
        db.close()


def _fetchone(sql, params=()):
    """Run a SELECT and return a single sqlite3.Row or None."""
    db = get_db()
    try:
        return db.execute(sql, params).fetchone()
    finally:
        db.close()


def _execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE and return the rowcount.

    Uses its own connection so callers never manage transactions.
    """
    db = get_db()
    try:
        cur = db.execute(sql, params)
        db.commit()
        return cur.rowcount
    finally:
        db.close()


def _insert(sql, params=()):
    """Run an INSERT and return the new row id."""
    db = get_db()
    try:
        cur = db.execute(sql, params)
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


# ── Timestamps ──────────────────────────────────────────────

def now_str():
    """Current local datetime as a DB string."""
    return datetime.now().replace(second=0, microsecond=0).strftime(DATETIME_DB)


def today_str():
    """Today's date as ISO string."""
    return date.today().isoformat()


# ── Schema ──────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'admin' CHECK(role IN ('admin','agent')),
    full_name     TEXT NOT NULL DEFAULT '',
    phone         TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('active','pending','suspended')),
    access_expires TEXT DEFAULT NULL,
    invite_code    TEXT DEFAULT '',
    invite_uses    INTEGER NOT NULL DEFAULT 0,
    invite_max_uses INTEGER NOT NULL DEFAULT 0,
    failed_logins  INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS packages (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    speed TEXT DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS customers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name          TEXT NOT NULL,
    phone              TEXT DEFAULT '',
    phone2             TEXT DEFAULT '',
    whatsapp_phone     TEXT DEFAULT '',
    address            TEXT DEFAULT '',
    region             TEXT DEFAULT '',
    package_id         INTEGER REFERENCES packages(id),
    mikrotik_username  TEXT DEFAULT '',
    mikrotik_password  TEXT DEFAULT '',
    nano_ip            TEXT DEFAULT '',
    device_type        TEXT DEFAULT '',
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK(status IN ('active','expired','suspended','inactive')),
    is_active          INTEGER NOT NULL DEFAULT 1,
    subscription_date  TEXT,
    renewal_date       TEXT,
    previous_debt      INTEGER NOT NULL DEFAULT 0,
    notes              TEXT DEFAULT '',
    created_at         TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS invoices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    month         INTEGER NOT NULL,
    year          INTEGER NOT NULL,
    package_name  TEXT DEFAULT '',
    package_price INTEGER DEFAULT 0,
    total_amount  INTEGER NOT NULL DEFAULT 0,
    paid_amount   INTEGER DEFAULT 0,
    is_paid       INTEGER NOT NULL DEFAULT 0,
    previous_debt INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(customer_id, month, year)
);

CREATE TABLE IF NOT EXISTS payments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id     INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    customer_id    INTEGER REFERENCES customers(id),
    amount         INTEGER NOT NULL,
    payment_date   TEXT NOT NULL,
    payment_method TEXT DEFAULT 'نقدي',
    notes          TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS expenses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_date   TEXT NOT NULL,
    category       TEXT DEFAULT 'أخرى',
    amount         INTEGER NOT NULL,
    description    TEXT DEFAULT '',
    recipient_name TEXT DEFAULT '',
    subscriber_id  INTEGER REFERENCES customers(id),
    created_at     TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS invoice_extras (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    item_name  TEXT NOT NULL,
    item_price INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS maintenance_tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id       INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    issue_description TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT DEFAULT (datetime('now','localtime')),
    resolved_at       TEXT
);

CREATE TABLE IF NOT EXISTS network_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    ip         TEXT DEFAULT '',
    link_type  TEXT NOT NULL DEFAULT 'MikroTik'
               CHECK(link_type IN ('MikroTik','Ubnt','Mimosa')),
    location   TEXT DEFAULT '',
    notes      TEXT DEFAULT '',
    username   TEXT DEFAULT '',
    password   TEXT DEFAULT '',
    community  TEXT DEFAULT 'public',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    username    TEXT DEFAULT '',
    action      TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id   INTEGER,
    details     TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS generator_info (
    id          INTEGER PRIMARY KEY CHECK(id = 1),
    owner_name  TEXT DEFAULT '',
    company_name TEXT DEFAULT 'الناظم',
    phone       TEXT DEFAULT '',
    address     TEXT DEFAULT '',
    footer_note TEXT DEFAULT 'شكراً لتعاملكم معنا'
);

CREATE TABLE IF NOT EXISTS signal_cache (
    ip           TEXT PRIMARY KEY,
    signal_dbm   TEXT,
    ccq          TEXT,
    rx_dbm       TEXT,
    tx_dbm       TEXT,
    status       TEXT DEFAULT 'offline',
    last_updated TEXT DEFAULT ''
);

-- ── Desktop PC activation (Phase 14.7: HWID activation codes) ──
CREATE TABLE IF NOT EXISTS device_activations (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hwid           TEXT NOT NULL,
    activation_key TEXT NOT NULL UNIQUE,
    expires_at     TEXT DEFAULT NULL,
    created_at     TEXT DEFAULT (datetime('now','localtime')),
    UNIQUE(user_id, hwid)
);
"""


def _rebuild_table_if_mismatched(table, required_cols):
    """Drop + recreate a table when its existing columns don't match the spec.

    Phase 10 migration: the pre-reboot database used SQLAlchemy models whose
    `settings` table had a different shape. CREATE TABLE IF NOT EXISTS would
    silently keep the old incompatible structure, so we rebuild it.

    Only seed tables (`settings` / `generator_info` / `packages`) — small and
    auto-reseeded — are handled this way. Business tables are migrated with
    `_migrate_legacy_business_tables()` which preserves every row.
    """
    db = get_db()
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {r[1] for r in rows}
        if not required_cols.issubset(cols):
            db.execute(f"DROP TABLE IF EXISTS {table}")
            db.commit()
    finally:
        db.close()


def _table_cols(table):
    """Return the set of column names for a table (empty if missing)."""
    db = get_db()
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        db.close()


def _migrate_legacy_business_tables():
    """Phase 10 data-preserving migration from the pre-reboot ORM schema.

    Phase 12 also ensures the `customers` table has the `previous_debt`
    column (added in-place via ALTER TABLE, preserving all rows).

    The old SQLAlchemy `customers` table used different column names
    (name/username/password/ip_address/subscription_status). We rebuild it
    with the new spec and copy every row across with the proper mapping —
    no data is lost.

    The old multi-tenant tables (invoices/payments/expenses/invoice_extras/
    maintenance_tickets) carried an `owner_id` column that the new schema
    drops; SQLite 3.35+ supports DROP COLUMN, so we remove it in place.
    """
    # ── customers: rename-migrate if it lacks full_name ──
    cols = _table_cols("customers")
    if cols and "full_name" not in cols:
        # Open a dedicated connection WITHOUT foreign_keys so the SQLite
        # rebuild recipe (CREATE -> COPY -> DROP -> RENAME) can drop the old
        # table even though child tables still reference it by FK.
        db = sqlite3.connect(LOCAL_DB_PATH)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN")
            # Clean up a leftover table from an interrupted previous migration.
            db.execute("DROP TABLE IF EXISTS customers_new")
            db.executescript("""
                CREATE TABLE customers_new (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    full_name          TEXT NOT NULL,
                    phone              TEXT DEFAULT '',
                    phone2             TEXT DEFAULT '',
                    whatsapp_phone     TEXT DEFAULT '',
                    address            TEXT DEFAULT '',
                    region             TEXT DEFAULT '',
                    package_id         INTEGER REFERENCES packages(id),
                    mikrotik_username  TEXT DEFAULT '',
                    mikrotik_password  TEXT DEFAULT '',
                    nano_ip            TEXT DEFAULT '',
                    device_type        TEXT DEFAULT '',
                    status             TEXT NOT NULL DEFAULT 'active'
                                       CHECK(status IN ('active','expired','suspended','inactive')),
                    is_active          INTEGER NOT NULL DEFAULT 1,
                    subscription_date  TEXT,
                    renewal_date       TEXT,
                    notes              TEXT DEFAULT '',
                    created_at         TEXT DEFAULT (datetime('now','localtime'))
                );
                """)
            # Copy with column mapping (old name -> new name), all rows preserved.
            db.execute(
                "INSERT INTO customers_new (id, full_name, phone, phone2, whatsapp_phone, "
                "address, region, package_id, mikrotik_username, mikrotik_password, nano_ip, "
                "device_type, status, is_active, subscription_date, renewal_date, notes, created_at) "
                "SELECT id, name, COALESCE(phone,''), COALESCE(phone2,''), "
                "COALESCE(whatsapp_phone,''), COALESCE(address,''), COALESCE(region,''), "
                "NULL, COALESCE(username,''), COALESCE(password,''), COALESCE(ip_address,''), "
                "COALESCE(device_type,''), COALESCE(subscription_status,'active'), "
                "CASE WHEN is_active = 1 THEN 1 ELSE 0 END, "
                "subscription_date, renewal_date, COALESCE(notes,''), created_at "
                "FROM customers"
            )
            db.execute("DROP TABLE customers")
            db.execute("ALTER TABLE customers_new RENAME TO customers")
            db.execute("COMMIT")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ── Phase 12: ensure customers has previous_debt (add in place) ──
    cols = _table_cols("customers")
    if cols and "previous_debt" not in cols:
        db = get_db()
        try:
            db.execute("ALTER TABLE customers ADD COLUMN previous_debt INTEGER NOT NULL DEFAULT 0")
            db.commit()
        except sqlite3.OperationalError:
            pass  # column may already exist in a concurrent boot
        finally:
            db.close()

    # ── Rebuild legacy child tables with the new schema (data preserved) ──
    # The pre-reboot ORM invoice/payment/etc. tables lacked ON DELETE CASCADE
    # and carried the obsolete owner_id / outage_deduction columns. Rebuild
    # each with the spec schema and copy every row across (drop owner_id /
    # outage_deduction — those columns are obsolete).
    rebuild_plans = [
        (
            "invoices",
            """
            CREATE TABLE invoices_new (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id   INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                month         INTEGER NOT NULL,
                year          INTEGER NOT NULL,
                package_name  TEXT DEFAULT '',
                package_price INTEGER DEFAULT 0,
                total_amount  INTEGER NOT NULL DEFAULT 0,
                paid_amount   INTEGER DEFAULT 0,
                is_paid       INTEGER NOT NULL DEFAULT 0,
                previous_debt INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(customer_id, month, year)
            );
            """,
            """
            INSERT INTO invoices_new (id, customer_id, month, year, package_name,
                package_price, total_amount, paid_amount, is_paid, previous_debt, created_at)
            SELECT id, customer_id, month, year, COALESCE(package_name,''),
                COALESCE(package_price,0), COALESCE(total_amount,0),
                COALESCE(paid_amount,0), COALESCE(is_paid,0),
                COALESCE(previous_debt,0), created_at
            FROM invoices
            """,
        ),
        (
            "payments",
            """
            CREATE TABLE payments_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id     INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                customer_id    INTEGER REFERENCES customers(id),
                amount         INTEGER NOT NULL,
                payment_date   TEXT NOT NULL,
                payment_method TEXT DEFAULT 'نقدي',
                notes          TEXT DEFAULT '',
                created_at     TEXT DEFAULT (datetime('now','localtime'))
            );
            """,
            """
            INSERT INTO payments_new (id, invoice_id, customer_id, amount,
                payment_date, payment_method, notes, created_at)
            SELECT id, invoice_id, customer_id, COALESCE(amount,0),
                COALESCE(payment_date,''), COALESCE(payment_method,'نقدي'),
                COALESCE(notes,''), created_at
            FROM payments
            """,
        ),
        (
            "expenses",
            """
            CREATE TABLE expenses_new (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date   TEXT NOT NULL,
                category       TEXT DEFAULT 'أخرى',
                amount         INTEGER NOT NULL,
                description    TEXT DEFAULT '',
                recipient_name TEXT DEFAULT '',
                subscriber_id  INTEGER REFERENCES customers(id),
                created_at     TEXT DEFAULT (datetime('now','localtime'))
            );
            """,
            """
            INSERT INTO expenses_new (id, expense_date, category, amount,
                description, recipient_name, subscriber_id, created_at)
            SELECT id, COALESCE(expense_date,''), COALESCE(category,'أخرى'),
                COALESCE(amount,0), COALESCE(description,''),
                COALESCE(recipient_name,''), subscriber_id, created_at
            FROM expenses
            """,
        ),
        (
            "invoice_extras",
            """
            CREATE TABLE invoice_extras_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
                item_name  TEXT NOT NULL,
                item_price INTEGER NOT NULL DEFAULT 0
            );
            """,
            """
            INSERT INTO invoice_extras_new (id, invoice_id, item_name, item_price)
            SELECT id, invoice_id, COALESCE(item_name,''), COALESCE(item_price,0)
            FROM invoice_extras
            """,
        ),
        (
            "maintenance_tickets",
            """
            CREATE TABLE maintenance_tickets_new (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id       INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
                issue_description TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        TEXT DEFAULT (datetime('now','localtime')),
                resolved_at       TEXT
            );
            """,
            """
            INSERT INTO maintenance_tickets_new (id, customer_id, issue_description,
                status, created_at, resolved_at)
            SELECT id, customer_id, COALESCE(issue_description,''),
                COALESCE(status,'pending'), created_at, resolved_at
            FROM maintenance_tickets
            """,
        ),
    ]

    for table, create_sql, copy_sql in rebuild_plans:
        tcols = _table_cols(table)
        if not tcols:
            continue  # table doesn't exist yet (fresh DB) — nothing to migrate
        db = sqlite3.connect(LOCAL_DB_PATH)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = OFF")
            db.execute("BEGIN")
            db.execute(f"DROP TABLE IF EXISTS {table}_new")
            db.execute(create_sql)
            db.execute(copy_sql)
            db.execute(f"DROP TABLE {table}")
            db.execute(f"ALTER TABLE {table}_new RENAME TO {table}")
            db.execute("COMMIT")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def init_db():
    """Create all tables and seed defaults (packages, admin, settings, generator_info)."""
    # Phase 10 migration: rebuild small auto-reseeded tables if the legacy
    # SQLAlchemy schema left an incompatible column layout behind.
    _rebuild_table_if_mismatched("settings", {"key", "value"})
    _rebuild_table_if_mismatched("generator_info", {"id", "owner_name", "company_name",
                                                    "phone", "address", "footer_note"})
    _rebuild_table_if_mismatched("packages", {"id", "name", "speed", "price"})
    _rebuild_table_if_mismatched("audit_log", {"id", "user_id", "username", "action",
                                               "target_type", "target_id", "details", "created_at"})

    db = get_db()
    try:
        db.executescript(SCHEMA_SQL)
        db.commit()
    finally:
        db.close()

    # Data-preserving migration of the pre-reboot ORM business tables.
    _migrate_legacy_business_tables()

    # ── Phase 14.4: ensure network_links has auth columns (add in place) ──
    lcols = _table_cols("network_links")
    if lcols:
        for col, ddl in (
            ("username", "ALTER TABLE network_links ADD COLUMN username TEXT DEFAULT ''"),
            ("password", "ALTER TABLE network_links ADD COLUMN password TEXT DEFAULT ''"),
            ("community", "ALTER TABLE network_links ADD COLUMN community TEXT DEFAULT 'public'"),
        ):
            if col not in lcols:
                db = get_db()
                try:
                    db.execute(ddl)
                    db.commit()
                except sqlite3.OperationalError:
                    pass  # column may already exist in a concurrent boot
                finally:
                    db.close()

    # ── Phase 14.4: ensure users has admin/access columns (add in place) ──
    ucols = _table_cols("users")
    if ucols:
        for col, ddl in (
            ("status", "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"),
            ("access_expires", "ALTER TABLE users ADD COLUMN access_expires TEXT DEFAULT NULL"),
            ("invite_code", "ALTER TABLE users ADD COLUMN invite_code TEXT DEFAULT ''"),
            ("invite_uses", "ALTER TABLE users ADD COLUMN invite_uses INTEGER NOT NULL DEFAULT 0"),
            ("invite_max_uses", "ALTER TABLE users ADD COLUMN invite_max_uses INTEGER NOT NULL DEFAULT 0"),
            ("failed_logins", "ALTER TABLE users ADD COLUMN failed_logins INTEGER NOT NULL DEFAULT 0"),
            ("created_at", "ALTER TABLE users ADD COLUMN created_at TEXT DEFAULT (datetime('now','localtime'))"),
            ("hwid", "ALTER TABLE users ADD COLUMN hwid TEXT DEFAULT ''"),
        ):
            if col not in ucols:
                db = get_db()
                try:
                    db.execute(ddl)
                    db.commit()
                except sqlite3.OperationalError:
                    pass  # column may already exist in a concurrent boot
                finally:
                    db.close()

    # ── Phase 14.7: ensure customers has owner_user_id (render linking) ──
    ccols = _table_cols("customers")
    if ccols and "owner_user_id" not in ccols:
        db = get_db()
        try:
            db.execute("ALTER TABLE customers ADD COLUMN owner_user_id INTEGER DEFAULT NULL")
            db.commit()
        except sqlite3.OperationalError:
            pass  # column may already exist in a concurrent boot
        finally:
            db.close()

    seed_default_packages()
    seed_default_admin()
    seed_default_settings()
    seed_generator_info()


# ── Seeds ───────────────────────────────────────────────────

def seed_default_packages():
    """Insert the standard ISP packages if the table is empty (idempotent)."""
    db = get_db()
    try:
        count = db.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        if count == 0:
            db.executemany(
                "INSERT INTO packages (name, speed, price) VALUES (?, ?, ?)",
                DEFAULT_PACKAGES,
            )
            db.commit()
    finally:
        db.close()


def seed_default_admin():
    """Create or update the admin account using env credentials.

    Uses config.SUPERADMIN_USERNAME / config.SUPERADMIN_PASSWORD:
      - no users at all  -> creates the admin from env (falls back to
        DEFAULT_ADMIN_* for local dev when env isn't set).
      - admin username already exists -> updates its password hash + role to
        keep it in sync with env (so an old 'admin/admin123' account becomes
        the env credentials).
    """
    username = (SUPERADMIN_USERNAME or "").strip() or DEFAULT_ADMIN_USERNAME
    password = SUPERADMIN_PASSWORD or DEFAULT_ADMIN_PASSWORD
    db = get_db()
    try:
        existing = db.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            db.execute(
                "UPDATE users SET password_hash = ?, role = 'admin', "
                "status = 'active', failed_logins = 0 WHERE id = ?",
                (generate_password_hash(password), existing["id"]),
            )
        else:
            db.execute(
                "INSERT INTO users (username, password_hash, role, full_name, status) "
                "VALUES (?, ?, 'admin', 'مدير النظام', 'active')",
                (username, generate_password_hash(password)),
            )
        db.commit()
    finally:
        db.close()


def seed_default_settings():
    """Populate the settings table with defaults when missing."""
    db = get_db()
    try:
        for key, value in DEFAULT_SETTINGS.items():
            exists = db.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone()
            if not exists:
                db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, value))
        db.commit()
    finally:
        db.close()


def seed_generator_info():
    """Ensure a single generator_info row (id=1) exists."""
    db = get_db()
    try:
        row = db.execute("SELECT 1 FROM generator_info WHERE id = 1").fetchone()
        if not row:
            db.execute(
                "INSERT INTO generator_info (owner_name, company_name, phone, address, footer_note) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    DEFAULT_GENERATOR_INFO["owner_name"],
                    DEFAULT_GENERATOR_INFO["company_name"],
                    DEFAULT_GENERATOR_INFO["phone"],
                    DEFAULT_GENERATOR_INFO["address"],
                    DEFAULT_GENERATOR_INFO["footer_note"],
                ),
            )
            db.commit()
    finally:
        db.close()


# ── Users ───────────────────────────────────────────────────

def get_user_by_username(username):
    """Return a user row by username, or None."""
    return _fetchone("SELECT * FROM users WHERE username = ?", (username,))


def get_user_by_id(user_id):
    """Return a user row by id, or None."""
    return _fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


def create_user(username, password, role="agent", full_name="", phone=""):
    """Create a new active user. Raises sqlite3.IntegrityError on duplicate username."""
    return _insert(
        "INSERT INTO users (username, password_hash, role, full_name, phone, status) "
        "VALUES (?, ?, ?, ?, ?, 'active')",
        (username, generate_password_hash(password), role, full_name, phone),
    )


def update_user(user_id, **fields):
    """Update allowed user fields in a single safe statement.

    Supports role/profile fields plus the admin-center fields
    (status, access_expires, invite_code, invite_uses, invite_max_uses,
    failed_logins) and password reset via the 'password' key.
    """
    allowed = {
        "username", "role", "full_name", "phone",
        "status", "access_expires", "invite_code",
        "invite_uses", "invite_max_uses", "failed_logins",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return
    if "password" in fields and fields["password"]:
        updates = {"password_hash": generate_password_hash(fields["password"]), **updates}
    cols = ", ".join(f"{key} = ?" for key in updates)
    _execute(f"UPDATE users SET {cols} WHERE id = ?", (*updates.values(), user_id))


def verify_password(user_row, password):
    """Check a plaintext password against a user row's hash."""
    return check_password_hash(user_row["password_hash"], password)


def list_users():
    """Return all users."""
    return _fetchall("SELECT * FROM users ORDER BY id")


def delete_user(user_id):
    """Delete a user by id."""
    _execute("DELETE FROM users WHERE id = ?", (user_id,))


def delete_all_agents():
    """Delete every non-admin user (agents) — used by the server reset tool.

    Keeps only users whose role is 'admin' so the owner account survives.
    Returns the number of deleted users.
    """
    return _execute("DELETE FROM users WHERE role != 'admin' OR role IS NULL")


# ── Registration / Invite / Access (Phase 14.4) ────────────

def create_registration(full_name, username, password, phone="", invite_code=""):
    """Create a pending-registration user (or active when a valid invite code).

    Args:
        full_name: display name.
        username: unique login name.
        password: plaintext password (hashed before storage).
        phone: optional phone.
        invite_code: when a valid code is supplied the account is created
            active and the code's use counter is incremented; otherwise the
            account is created with status 'pending' and the admin approves.

    Returns:
        dict with {"id": user_id, "status": "active"|"pending"}.

    Raises:
        sqlite3.IntegrityError on duplicate username.
    """
    status = "active" if invite_code and redeem_invite_code(invite_code) else "pending"
    user_id = _insert(
        "INSERT INTO users (username, password_hash, role, full_name, phone, status) "
        "VALUES (?, ?, 'agent', ?, ?, ?)",
        (username, generate_password_hash(password), full_name, phone, status),
    )
    if status == "active" and invite_code:
        # Record which code activated the account for auditability.
        _execute(
            "UPDATE users SET invite_code = ? WHERE id = ?",
            (invite_code, user_id),
        )
    return {"id": user_id, "status": status}


def redeem_invite_code(code):
    """Consume one use of an invite code and return True when valid.

    A code is valid when it exists on an active user, has remaining uses
    (invite_uses < invite_max_uses), and the owner isn't suspended/expired.
    """
    code = (code or "").strip()
    if not code:
        return False
    row = _fetchone(
        "SELECT * FROM users WHERE invite_code = ? AND invite_max_uses > 0 "
        "AND invite_uses < invite_max_uses AND status = 'active'",
        (code,),
    )
    if not row:
        return False
    if row["access_expires"] and str(row["access_expires"]) < now_str():
        return False
    _execute(
        "UPDATE users SET invite_uses = invite_uses + 1 WHERE id = ?",
        (row["id"],),
    )
    return True


def approve_user(user_id):
    """Approve a pending registration (status -> active). Returns True on success."""
    return _execute(
        "UPDATE users SET status = 'active', failed_logins = 0 WHERE id = ? AND status = 'pending'",
        (user_id,),
    ) > 0


def reject_user(user_id):
    """Delete a pending registration (rejection)."""
    return _execute(
        "DELETE FROM users WHERE id = ? AND status = 'pending'",
        (user_id,),
    ) > 0


def set_user_status(user_id, status):
    """Set a user's status (active/suspended). Pending must be approved."""
    if status not in ("active", "suspended"):
        return False
    return _execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id)) > 0


def set_user_access_expiry(user_id, expires_dt):
    """Set/clear a user's access expiry timestamp (None = unlimited)."""
    if expires_dt is None:
        return _execute("UPDATE users SET access_expires = NULL WHERE id = ?", (user_id,)) > 0
    return _execute(
        "UPDATE users SET access_expires = ? WHERE id = ?",
        (str(expires_dt), user_id),
    ) > 0


def grant_timed_access(user_id, duration_hours=0, duration_days=0):
    """Grant timed access: extend a user's access_expires by the given duration.

    Args:
        user_id: target user.
        duration_hours: hours to add.
        duration_days: days to add (combined with hours).
    """
    from datetime import timedelta

    total = timedelta(days=duration_days or 0, hours=duration_hours or 0)
    if total.total_seconds() <= 0:
        return False
    row = get_user_by_id(user_id)
    if not row:
        return False
    base = datetime.now()
    if row["access_expires"]:
        try:
            base = datetime.strptime(str(row["access_expires"]), DATETIME_DB)
        except ValueError:
            base = datetime.now()
    new_expiry = base + total
    return _execute(
        "UPDATE users SET access_expires = ?, status = 'active' WHERE id = ?",
        (new_expiry.strftime(DATETIME_DB), user_id),
    ) > 0


def generate_invite_code(user_id, max_uses=1):
    """Generate a unique invite code for a user (max uses). Returns the code."""
    import secrets

    if max_uses < 1:
        max_uses = 1
    code = secrets.token_hex(4).upper()  # e.g. "1F2A-9B3C" (8 chars)
    _execute(
        "UPDATE users SET invite_code = ?, invite_max_uses = ?, invite_uses = 0 "
        "WHERE id = ?",
        (code, max_uses, user_id),
    )
    return code


def count_failed_logins(user_id):
    """Return the number of consecutive failed login attempts for a user."""
    row = _fetchone("SELECT failed_logins FROM users WHERE id = ?", (user_id,))
    return int(row["failed_logins"] or 0) if row else 0


def increment_failed_logins(user_id):
    """Increment a user's failed-login counter by 1."""
    _execute(
        "UPDATE users SET failed_logins = failed_logins + 1 WHERE id = ?",
        (user_id,),
    )


def reset_failed_logins(user_id):
    """Reset a user's failed-login counter to 0."""
    _execute("UPDATE users SET failed_logins = 0 WHERE id = ?", (user_id,))


def lock_user(user_id):
    """Suspend a user after too many failed logins (auto-lockout)."""
    _execute(
        "UPDATE users SET status = 'suspended', failed_logins = 0 WHERE id = ?",
        (user_id,),
    )


def is_user_active(user_row, now=None):
    """Return True when a user can log in (active status and not expired).

    Args:
        user_row: a users row (dict-like: sqlite3.Row or dict).
        now: optional datetime to compare against (defaults to now).
    """
    if not user_row:
        return False
    if isinstance(user_row, dict):
        status = str(user_row.get("status") or "active")
        expires = user_row.get("access_expires")
    else:
        try:
            status = str(user_row["status"] or "active")
        except (KeyError, IndexError):
            status = "active"
        try:
            expires = user_row["access_expires"]
        except (KeyError, IndexError):
            expires = None
    if status != "active":
        return False
    if expires:
        try:
            exp = datetime.strptime(str(expires), DATETIME_DB)
            if (now or datetime.now()) > exp:
                return False
        except ValueError:
            return False
    return True


# ── Desktop PC Activation (Phase 14.7: HWID) ───────────────

def save_device_activation(user_id, hwid, activation_key, expires_at=None):
    """Store a desktop activation record (one per user+hwid).

    Args:
        user_id: the target user's id the PC is being activated for.
        hwid: the desktop's hardware id string.
        activation_key: the signed activation key shown to the user.
        expires_at: optional DB datetime string (None = permanent).

    Returns:
        int: the new activation row id.
    """
    return _insert(
        "INSERT INTO device_activations (user_id, hwid, activation_key, expires_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, hwid) DO UPDATE SET "
        "activation_key = excluded.activation_key, expires_at = excluded.expires_at",
        (user_id, hwid, activation_key, expires_at),
    )


def get_device_activation_by_key(activation_key):
    """Return a device_activations row matching a key, or None."""
    return _fetchone(
        "SELECT * FROM device_activations WHERE activation_key = ?",
        (activation_key,),
    )


def get_device_activation(user_id, hwid):
    """Return a device_activations row for a user+hwid, or None."""
    return _fetchone(
        "SELECT * FROM device_activations WHERE user_id = ? AND hwid = ?",
        (user_id, hwid),
    )


def list_device_activations():
    """Return all activation records (admin center)."""
    return _fetchall(
        "SELECT da.*, u.username, u.full_name "
        "FROM device_activations da JOIN users u ON u.id = da.user_id "
        "ORDER BY da.created_at DESC"
    )


def revoke_device_activation(activation_id):
    """Delete an activation record by id. Returns True when deleted."""
    return _execute(
        "DELETE FROM device_activations WHERE id = ?", (activation_id,)
    ) > 0


def set_user_hwid(user_id, hwid):
    """Store the bound HWID on the user record (local EXE keeps it in sync)."""
    return _execute(
        "UPDATE users SET hwid = ? WHERE id = ?", (hwid or "", user_id)
    ) > 0


# ── Audit Log ──────────────────────────────────────────────

def log_action(user_id=None, username="", action="", target_type="", target_id=None, details=""):
    """Record an audit trail entry (who did what).

    Args:
        user_id: acting user's id (may be None for system actions).
        username: acting user's username/name.
        action: short Arabic action verb (e.g. 'تسجيل دخول', 'دفع').
        target_type: affected entity ('customer', 'payment', 'package', ...).
        target_id: id of the affected entity.
        details: human-readable Arabic detail string.
    """
    return _insert(
        "INSERT INTO audit_log (user_id, username, action, target_type, target_id, details) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, username, action, target_type, target_id, details),
    )


def list_audit_logs(limit=200):
    """Return recent audit entries, newest first."""
    return _fetchall(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )


def audit_log_count():
    """Return the total number of audit entries."""
    row = _fetchone("SELECT COUNT(*) AS n FROM audit_log")
    return row["n"] if row else 0


# ── Settings ────────────────────────────────────────────────

def get_settings():
    """Return the settings table as a dict."""
    rows = _fetchall("SELECT key, value FROM settings")
    return {r["key"]: r["value"] for r in rows}


def set_setting(key, value):
    """Upsert a single setting."""
    db = get_db()
    try:
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        db.commit()
    finally:
        db.close()


def get_generator_info():
    """Return the generator_info row (company profile) or None."""
    return _fetchone("SELECT * FROM generator_info WHERE id = 1")


def update_generator_info(owner_name=None, phone=None, address=None, footer_note=None):
    """Update company profile fields (empty strings are preserved as provided)."""
    db = get_db()
    try:
        db.execute(
            "UPDATE generator_info SET "
            "owner_name = COALESCE(?, owner_name), "
            "phone     = COALESCE(?, phone), "
            "address   = COALESCE(?, address), "
            "footer_note = COALESCE(?, footer_note) "
            "WHERE id = 1",
            (owner_name, phone, address, footer_note),
        )
        db.commit()
    finally:
        db.close()


# ── Packages ────────────────────────────────────────────────

def list_packages():
    """Return all packages ordered by id."""
    return _fetchall("SELECT id, name, speed, price FROM packages ORDER BY id")


def get_package(package_id):
    """Return a package row by id, or None."""
    return _fetchone("SELECT * FROM packages WHERE id = ?", (package_id,))


def get_package_by_name(name):
    """Return a package row by name, or None."""
    return _fetchone("SELECT * FROM packages WHERE name = ?", (name,))


def add_package(name, price, speed=""):
    """Insert a new package. Returns the new id."""
    return _insert(
        "INSERT INTO packages (name, speed, price) VALUES (?, ?, ?)",
        (name, speed, price),
    )


def update_package(package_id, name=None, price=None, speed=None):
    """Update a package's allowed fields."""
    fields = []
    values = []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if price is not None:
        fields.append("price = ?")
        values.append(price)
    if speed is not None:
        fields.append("speed = ?")
        values.append(speed)
    if not fields:
        return
    values.append(package_id)
    _execute(f"UPDATE packages SET {', '.join(fields)} WHERE id = ?", tuple(values))


def delete_package(package_id):
    """Delete a package by id."""
    _execute("DELETE FROM packages WHERE id = ?", (package_id,))


# ── Customers ───────────────────────────────────────────────

def _customer_select():
    """SELECT clause that aliases columns to the legacy template contract."""
    return (
        "SELECT c.id, c.full_name AS name, c.phone, c.phone2, c.whatsapp_phone, "
        "c.address, c.region, c.package_id, c.mikrotik_username AS username, "
        "c.mikrotik_password AS password, c.nano_ip AS ip_address, c.device_type, "
        "c.status AS subscription_status, c.is_active, c.subscription_date, "
        "c.renewal_date, c.previous_debt, c.notes, c.created_at, c.owner_user_id, "
        "p.name AS package_name, p.price AS package_price "
        "FROM customers c LEFT JOIN packages p ON p.id = c.package_id "
    )


def list_customers():
    """Return all customers with package join (legacy aliases)."""
    return _fetchall(_customer_select() + "ORDER BY c.created_at DESC, c.id DESC")


def query_customers(search="", status="all", region="", debt=False,
                    sort_by="created_at", sort_dir="desc"):
    """Return customers with optional filters (customers page).

    Args:
        search: match name / phone / phone2.
        status: 'all' | 'active' | 'expired' | 'suspended' | 'inactive'.
        region: exact region filter.
        debt: only customers with unpaid invoices.
        sort_by: column name in the customer_select alias set.
        sort_dir: 'asc' or 'desc'.
    """
    sql = _customer_select() + "WHERE 1 = 1 "
    params = []
    if search:
        like = f"%{search}%"
        sql += "AND (c.full_name LIKE ? OR c.phone LIKE ? OR c.phone2 LIKE ?) "
        params += [like, like, like]
    if status == "active":
        sql += "AND c.is_active = 1 AND c.status = 'active' "
    elif status == "expired":
        sql += "AND c.status = 'expired' "
    elif status == "suspended":
        sql += "AND c.status = 'suspended' "
    elif status == "inactive":
        sql += "AND c.is_active = 0 "
    if region:
        sql += "AND c.region = ? "
        params.append(region)
    if debt:
        sql += "AND c.id IN (SELECT customer_id FROM invoices WHERE is_paid = 0 " \
               "GROUP BY customer_id HAVING SUM(total_amount - paid_amount) > 0) "

    allowed_sorts = {
        "name": "c.full_name", "created_at": "c.created_at",
        "subscription_date": "c.subscription_date", "renewal_date": "c.renewal_date",
        "subscription_status": "c.status", "region": "c.region",
        "package_name": "p.name", "package_price": "p.price",
    }
    col = allowed_sorts.get(sort_by, "c.created_at")
    direction = "ASC" if sort_dir == "asc" else "DESC"
    sql += f"ORDER BY {col} {direction}, c.id DESC"
    return _fetchall(sql, tuple(params))


def get_customer(customer_id):
    """Return a single customer with package join, or None."""
    return _fetchone(_customer_select() + "WHERE c.id = ?", (customer_id,))


def get_customer_by_name(name):
    """Return a customer by exact full_name, or None."""
    return _fetchone(_customer_select() + "WHERE c.full_name = ?", (name,))


def search_customers(q, limit=50):
    """Search customers by name / phone / mikrotik_username."""
    like = f"%{q}%"
    return _fetchall(
        _customer_select()
        + "WHERE c.full_name LIKE ? OR c.phone LIKE ? OR c.mikrotik_username LIKE ? "
        + "ORDER BY c.full_name LIMIT ?",
        (like, like, like, limit),
    )


def add_customer(full_name, phone="", phone2="", whatsapp_phone="", address="",
                 region="", package_id=None, mikrotik_username="",
                 mikrotik_password="", nano_ip="", device_type="",
                 subscription_date=None, renewal_date=None, status="active",
                 previous_debt=0, notes="", owner_user_id=None):
    """Insert a customer. Returns the new id."""
    return _insert(
        "INSERT INTO customers (full_name, phone, phone2, whatsapp_phone, address, "
        "region, package_id, mikrotik_username, mikrotik_password, nano_ip, "
        "device_type, subscription_date, renewal_date, status, is_active, "
        "previous_debt, notes, owner_user_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            full_name, phone, phone2, whatsapp_phone, address,
            region, package_id, mikrotik_username, mikrotik_password, nano_ip,
            device_type, subscription_date or now_str(), renewal_date, status,
            1 if status in ("active",) else 0, previous_debt or 0, notes,
            owner_user_id,
        ),
    )


def update_customer(customer_id, **fields):
    """Update allowed customer fields in one safe statement.

    If 'name' is provided it maps to full_name; 'username'/'password'/'ip_address'
    map to mikrotik_* / nano_ip so callers keep the legacy API contract.
    """
    col_map = {
        "name": "full_name",
        "phone": "phone",
        "phone2": "phone2",
        "whatsapp_phone": "whatsapp_phone",
        "address": "address",
        "region": "region",
        "package_id": "package_id",
        "username": "mikrotik_username",
        "password": "mikrotik_password",
        "ip_address": "nano_ip",
        "device_type": "device_type",
        "status": "status",
        "subscription_status": "status",
        "renewal_date": "renewal_date",
        "subscription_date": "subscription_date",
        "previous_debt": "previous_debt",
        "notes": "notes",
        "owner_user_id": "owner_user_id",
    }
    updates = {}
    for key, value in fields.items():
        if key in col_map and value is not None:
            updates[col_map[key]] = value
    if "status" in updates:
        updates["is_active"] = 1 if updates["status"] == "active" else 0
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    _execute(f"UPDATE customers SET {cols} WHERE id = ?", (*updates.values(), customer_id))


def update_customer_legacy_fields(customer_id, package_name="", package_price=0):
    """Update the denormalized package_name/price on a customer's invoices.

    Keeps existing invoices consistent when the customer's package is edited
    (the pre-reboot ORM stored package_name/package_price on Customer directly).
    """
    db = get_db()
    try:
        db.execute(
            "UPDATE invoices SET package_name = ?, package_price = ? "
            "WHERE customer_id = ?",
            (package_name, package_price, customer_id),
        )
        db.commit()
    finally:
        db.close()


def set_customer_status(customer_id, status):
    """Set subscription status + matching is_active in one transaction."""
    db = get_db()
    try:
        db.execute(
            "UPDATE customers SET status = ?, is_active = ? WHERE id = ?",
            (status, 1 if status == "active" else 0, customer_id),
        )
        db.commit()
    finally:
        db.close()


def toggle_customer(customer_id):
    """Flip is_active."""
    db = get_db()
    try:
        db.execute("UPDATE customers SET is_active = NOT is_active WHERE id = ?", (customer_id,))
        db.commit()
    finally:
        db.close()


def delete_customer(customer_id):
    """Delete a customer (cascades invoices/payments/extras/tickets)."""
    _execute("DELETE FROM customers WHERE id = ?", (customer_id,))


def customer_regions():
    """Distinct non-empty regions."""
    rows = _fetchall(
        "SELECT DISTINCT region FROM customers WHERE region IS NOT NULL AND region != '' ORDER BY region"
    )
    return [r["region"] for r in rows]


def customer_stats():
    """Basic counts: total / active / expired / suspended."""
    row = _fetchone(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN is_active = 1 AND status = 'active' THEN 1 ELSE 0 END) AS active, "
        "SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired, "
        "SUM(CASE WHEN status = 'suspended' THEN 1 ELSE 0 END) AS suspended "
        "FROM customers"
    )
    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "expired": row["expired"] or 0,
        "suspended": row["suspended"] or 0,
    }


# ── Invoices ────────────────────────────────────────────────

def get_invoice(invoice_id):
    """Return an invoice row by id, or None."""
    return _fetchone("SELECT * FROM invoices WHERE id = ?", (invoice_id,))


def get_invoice_with_customer(invoice_id):
    """Invoice JOIN customer row for the print templates, or None."""
    return _fetchone(_invoice_select() + "WHERE i.id = ?", (invoice_id,))


def get_customer_invoice(customer_id, month, year):
    """Return a specific month's invoice for a customer, or None."""
    return _fetchone(
        "SELECT * FROM invoices WHERE customer_id = ? AND month = ? AND year = ?",
        (customer_id, month, year),
    )


def list_customer_invoices(customer_id):
    """All invoices for a customer, newest first."""
    return _fetchall(
        "SELECT * FROM invoices WHERE customer_id = ? ORDER BY year DESC, month DESC",
        (customer_id,),
    )


def _invoice_select():
    """Invoice JOIN customer select with print/billing aliases.

    Templates access invoice.id / invoice.cust_id / invoice.customer_name /
    invoice.username / invoice.phone / invoice.address / invoice.renewal_date,
    so those customer columns are aliased into every invoice row.
    """
    return (
        "SELECT i.*, c.id AS cust_id, c.full_name AS customer_name, "
        "c.mikrotik_username AS username, c.phone, c.address, c.renewal_date, "
        "c.full_name AS customer_phone "
        "FROM invoices i JOIN customers c ON c.id = i.customer_id "
    )


def list_invoices(month, year, search=""):
    """Invoices for a given month/year (optionally filtered by customer name)."""
    sql = _invoice_select() + "WHERE i.month = ? AND i.year = ? "
    params = [month, year]
    if search:
        sql += "AND c.full_name LIKE ? "
        params.append(f"%{search}%")
    sql += "ORDER BY c.full_name"
    return _fetchall(sql, tuple(params))


def add_invoice(customer_id, month, year, package_name="", package_price=0,
                total_amount=0, paid_amount=0, is_paid=False, previous_debt=0):
    """Insert an invoice. Returns the new id."""
    return _insert(
        "INSERT INTO invoices (customer_id, month, year, package_name, package_price, "
        "total_amount, paid_amount, is_paid, previous_debt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            customer_id, month, year, package_name, package_price,
            total_amount, paid_amount, 1 if is_paid else 0, previous_debt,
        ),
    )


def update_invoice(invoice_id, **fields):
    """Update allowed invoice fields in one safe statement."""
    updates = {}
    for key in ("total_amount", "paid_amount", "package_name", "package_price", "previous_debt"):
        if key in fields and fields[key] is not None:
            updates[key] = fields[key]
    if "is_paid" in fields and fields["is_paid"] is not None:
        updates["is_paid"] = 1 if fields["is_paid"] else 0
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    _execute(f"UPDATE invoices SET {cols} WHERE id = ?", (*updates.values(), invoice_id))


def delete_invoice(invoice_id):
    """Delete an invoice (cascades payments/extras)."""
    _execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))


def invoices_for_month(month, year):
    """All invoices for a month/year (for billing/generate/rollover)."""
    return _fetchall("SELECT * FROM invoices WHERE month = ? AND year = ?", (month, year))


def count_invoices(month, year):
    """Count invoices for a month/year."""
    row = _fetchone("SELECT COUNT(*) AS n FROM invoices WHERE month = ? AND year = ?", (month, year))
    return row["n"] if row else 0


def count_paid_invoices(month, year):
    """Count fully-paid invoices for a month/year."""
    row = _fetchone(
        "SELECT COUNT(*) AS n FROM invoices WHERE month = ? AND year = ? AND is_paid = 1",
        (month, year),
    )
    return row["n"] if row else 0


def count_unpaid_invoices(month, year):
    """Count invoices for a month/year that are not fully paid."""
    row = _fetchone(
        "SELECT COUNT(*) AS n FROM invoices "
        "WHERE month = ? AND year = ? AND (is_paid = 0 OR paid_amount < total_amount)",
        (month, year),
    )
    return row["n"] if row else 0


def sum_invoice_amounts(month, year, column="total_amount"):
    """Sum a numeric invoice column for a month/year."""
    row = _fetchone(
        f"SELECT COALESCE(SUM({column}), 0) AS s FROM invoices WHERE month = ? AND year = ?",
        (month, year),
    )
    return row["s"] or 0


def customer_unpaid_debt(customer_id, up_to_month=None, up_to_year=None):
    """Sum of unpaid amounts for a customer's unpaid invoices.

    Optionally restricts to invoices <= (up_to_year, up_to_month) for rollover.
    """
    sql = (
        "SELECT COALESCE(SUM(total_amount - paid_amount), 0) AS s "
        "FROM invoices WHERE customer_id = ? AND is_paid = 0"
    )
    params = [customer_id]
    if up_to_month is not None and up_to_year is not None:
        sql += " AND (year < ? OR (year = ? AND month <= ?))"
        params += [up_to_year, up_to_year, up_to_month]
    row = _fetchone(sql, tuple(params))
    return row["s"] or 0


def total_unpaid_debt():
    """Sum of all unpaid invoice amounts across the system."""
    row = _fetchone(
        "SELECT COALESCE(SUM(total_amount - paid_amount), 0) AS s "
        "FROM invoices WHERE is_paid = 0"
    )
    return row["s"] or 0


def debts_summary():
    """Per-customer unpaid totals for the debts page."""
    return _fetchall(
        "SELECT c.id AS id, c.full_name AS name, c.phone, c.region, "
        "p.name AS package_name, p.price AS package_price, "
        "SUM(i.total_amount - i.paid_amount) AS total_debt, "
        "COUNT(i.id) AS unpaid_count "
        "FROM invoices i JOIN customers c ON c.id = i.customer_id "
        "LEFT JOIN packages p ON p.id = c.package_id "
        "WHERE i.is_paid = 0 "
        "GROUP BY c.id HAVING SUM(i.total_amount - i.paid_amount) > 0 "
        "ORDER BY SUM(i.total_amount - i.paid_amount) DESC"
    )


# ── Payments ────────────────────────────────────────────────

def get_payment(payment_id):
    """Return a payment row by id, or None."""
    return _fetchone("SELECT * FROM payments WHERE id = ?", (payment_id,))


def get_payment_details(payment_id):
    """Payment JOIN customer+package row for the receipt templates, or None."""
    return _fetchone(_payment_select() + "WHERE pay.id = ?", (payment_id,))


def list_invoice_payments(invoice_id):
    """All payments for an invoice, newest first."""
    return _fetchall(
        "SELECT * FROM payments WHERE invoice_id = ? ORDER BY payment_date DESC, id DESC",
        (invoice_id,),
    )


def list_customer_payments(customer_id):
    """All payments for a customer, newest first."""
    return _fetchall(
        "SELECT * FROM payments WHERE customer_id = ? ORDER BY payment_date DESC, id DESC",
        (customer_id,),
    )


def _payment_select():
    """Payment JOIN customer+package select with receipt-template aliases.

    Templates access payment.username / payment.package_name /
    payment.renewal_date, so those customer columns are aliased in.
    """
    return (
        "SELECT pay.*, c.full_name AS customer_name, pay.customer_id AS cust_id, "
        "c.mikrotik_username AS username, p.name AS package_name, c.renewal_date "
        "FROM payments pay JOIN customers c ON c.id = pay.customer_id "
        "LEFT JOIN packages p ON p.id = c.package_id "
    )


def list_recent_payments(search="", method="", limit=200):
    """Recent payments, optionally filtered by customer name / method."""
    sql = _payment_select() + "WHERE 1 = 1 "
    params = []
    if search:
        sql += "AND c.full_name LIKE ? "
        params.append(f"%{search}%")
    if method in ("نقدي", "تحويل", "بطاقة", "غير ذلك"):
        sql += "AND pay.payment_method = ? "
        params.append(method)
    sql += "ORDER BY pay.payment_date DESC, pay.id DESC LIMIT ?"
    params.append(limit)
    return _fetchall(sql, tuple(params))


def add_payment(invoice_id, customer_id, amount, payment_date, payment_method="نقدي", notes=""):
    """Insert a payment. Returns the new id."""
    return _insert(
        "INSERT INTO payments (invoice_id, customer_id, amount, payment_date, payment_method, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (invoice_id, customer_id, amount, payment_date, payment_method, notes),
    )


def update_payment(payment_id, **fields):
    """Update allowed payment fields in one safe statement."""
    updates = {}
    for key in ("amount", "payment_date", "payment_method", "notes"):
        if key in fields and fields[key] is not None:
            updates[key] = fields[key]
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    _execute(f"UPDATE payments SET {cols} WHERE id = ?", (*updates.values(), payment_id))


def delete_payment(payment_id):
    """Delete a payment by id."""
    _execute("DELETE FROM payments WHERE id = ?", (payment_id,))


def total_payments():
    """Sum of all payments."""
    row = _fetchone("SELECT COALESCE(SUM(amount), 0) AS s FROM payments")
    return row["s"] or 0


def total_payments_today():
    """Sum of today's payments."""
    row = _fetchone(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE payment_date = ?",
        (today_str(),),
    )
    return row["s"] or 0


# ── Expenses ────────────────────────────────────────────────

def list_expenses(month, year, search="", category=""):
    """Expenses for a month/year with optional filters (includes subscriber name)."""
    sql = (
        "SELECT e.*, c.full_name AS subscriber_name "
        "FROM expenses e LEFT JOIN customers c ON c.id = e.subscriber_id "
        "WHERE strftime('%m', e.expense_date) = ? AND strftime('%Y', e.expense_date) = ? "
    )
    params = [f"{month:02d}", str(year)]
    if search:
        sql += "AND (e.description LIKE ? OR e.category LIKE ?) "
        params += [f"%{search}%", f"%{search}%"]
    if category:
        sql += "AND e.category = ? "
        params.append(category)
    sql += "ORDER BY e.expense_date DESC, e.id DESC"
    return _fetchall(sql, tuple(params))


def add_expense(expense_date, category, amount, description="", recipient_name="", subscriber_id=None):
    """Insert an expense. Returns the new id."""
    return _insert(
        "INSERT INTO expenses (expense_date, category, amount, description, recipient_name, subscriber_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (expense_date, category, amount, description, recipient_name, subscriber_id),
    )


def update_expense(expense_id, **fields):
    """Update allowed expense fields."""
    updates = {}
    for key in ("expense_date", "category", "amount", "description", "recipient_name", "subscriber_id"):
        if key in fields and fields[key] is not None:
            updates[key] = fields[key]
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    _execute(f"UPDATE expenses SET {cols} WHERE id = ?", (*updates.values(), expense_id))


def delete_expense(expense_id):
    """Delete an expense by id."""
    _execute("DELETE FROM expenses WHERE id = ?", (expense_id,))


def get_expense(expense_id):
    """Return an expense row by id, or None."""
    return _fetchone("SELECT * FROM expenses WHERE id = ?", (expense_id,))


def total_expenses(month, year):
    """Sum of expenses for a month/year."""
    row = _fetchone(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM expenses "
        "WHERE strftime('%m', expense_date) = ? AND strftime('%Y', expense_date) = ?",
        (f"{month:02d}", str(year)),
    )
    return row["s"] or 0


def expense_categories(month, year):
    """Per-category expense totals for a month/year."""
    return _fetchall(
        "SELECT category, SUM(amount) AS total FROM expenses "
        "WHERE strftime('%m', expense_date) = ? AND strftime('%Y', expense_date) = ? "
        "GROUP BY category ORDER BY SUM(amount) DESC",
        (f"{month:02d}", str(year)),
    )


def list_active_customers():
    """Active customers, ordered by name (name alias for dropdowns)."""
    return _fetchall(
        "SELECT c.*, c.full_name AS name, p.name AS package_name, p.price AS package_price "
        "FROM customers c LEFT JOIN packages p ON p.id = c.package_id "
        "WHERE c.is_active = 1 AND c.status = 'active' "
        "ORDER BY c.full_name"
    )


# ── Invoice extras ──────────────────────────────────────────

def list_invoice_extras(invoice_id):
    """All extras attached to an invoice."""
    return _fetchall(
        "SELECT * FROM invoice_extras WHERE invoice_id = ? ORDER BY id",
        (invoice_id,),
    )


def extras_total(invoice_id):
    """Sum of item prices for an invoice's extras."""
    row = _fetchone(
        "SELECT COALESCE(SUM(item_price), 0) AS s FROM invoice_extras WHERE invoice_id = ?",
        (invoice_id,),
    )
    return row["s"] or 0


def add_invoice_extra(invoice_id, item_name, item_price):
    """Insert an invoice extra. Returns the new id."""
    return _insert(
        "INSERT INTO invoice_extras (invoice_id, item_name, item_price) VALUES (?, ?, ?)",
        (invoice_id, item_name, item_price),
    )


def delete_invoice_extra(extra_id):
    """Delete an invoice extra by id."""
    _execute("DELETE FROM invoice_extras WHERE id = ?", (extra_id,))


def get_invoice_extra(extra_id):
    """Return an invoice extra row by id, or None."""
    return _fetchone("SELECT * FROM invoice_extras WHERE id = ?", (extra_id,))


# ── Maintenance tickets ─────────────────────────────────────

def list_tickets(status="all", search=""):
    """Maintenance tickets with customer join (templates use t.phone)."""
    sql = (
        "SELECT t.*, c.full_name AS customer_name, c.phone AS customer_phone, "
        "c.phone AS phone "
        "FROM maintenance_tickets t JOIN customers c ON c.id = t.customer_id "
        "WHERE 1 = 1 "
    )
    params = []
    if status == "pending":
        sql += "AND t.status = 'pending' "
    elif status == "resolved":
        sql += "AND t.status = 'resolved' "
    if search:
        sql += "AND (c.full_name LIKE ? OR t.issue_description LIKE ?) "
        params += [f"%{search}%", f"%{search}%"]
    sql += "ORDER BY t.created_at DESC"
    return _fetchall(sql, tuple(params))


def add_ticket(customer_id, issue_description):
    """Insert a maintenance ticket. Returns the new id."""
    return _insert(
        "INSERT INTO maintenance_tickets (customer_id, issue_description, status) "
        "VALUES (?, ?, 'pending')",
        (customer_id, issue_description),
    )


def get_ticket(ticket_id):
    """Return a ticket by id, or None."""
    return _fetchone("SELECT * FROM maintenance_tickets WHERE id = ?", (ticket_id,))


def update_ticket(ticket_id, customer_id, issue_description):
    """Update a maintenance ticket's customer + description."""
    _execute(
        "UPDATE maintenance_tickets SET customer_id = ?, issue_description = ? WHERE id = ?",
        (customer_id, issue_description, ticket_id),
    )


def toggle_ticket(ticket_id):
    """Toggle a ticket's resolved/pending status in one statement."""
    db = get_db()
    try:
        db.execute(
            "UPDATE maintenance_tickets SET "
            "status = CASE WHEN status = 'pending' THEN 'resolved' ELSE 'pending' END, "
            "resolved_at = CASE WHEN status = 'pending' THEN ? ELSE NULL END "
            "WHERE id = ?",
            (now_str(), ticket_id),
        )
        db.commit()
        row = db.execute("SELECT status FROM maintenance_tickets WHERE id = ?", (ticket_id,)).fetchone()
        return row["status"] if row else None
    finally:
        db.close()


def delete_ticket(ticket_id):
    """Delete a ticket by id."""
    _execute("DELETE FROM maintenance_tickets WHERE id = ?", (ticket_id,))


# ── Signal Cache (Phase 14.6) ───────────────────────────────

def upsert_signal_batch(batch):
    """Upsert a batch of signal readings (one commit, safe with SQLite).

    Args:
        batch: iterable of dicts with keys: ip, signal_dbm, ccq, rx_dbm,
               tx_dbm, status, last_updated.
    """
    db = get_db()
    try:
        now = now_str()
        db.executemany(
            "INSERT INTO signal_cache (ip, signal_dbm, ccq, rx_dbm, tx_dbm, "
            "status, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ip) DO UPDATE SET "
            "signal_dbm = excluded.signal_dbm, ccq = excluded.ccq, "
            "rx_dbm = excluded.rx_dbm, tx_dbm = excluded.tx_dbm, "
            "status = excluded.status, last_updated = excluded.last_updated",
            [
                (
                    r.get("ip", ""),
                    str(r.get("signal_dbm") or "") if r.get("signal_dbm") is not None else "",
                    str(r.get("ccq") or "") if r.get("ccq") is not None else "",
                    str(r.get("rx_dbm") or "") if r.get("rx_dbm") is not None else "",
                    str(r.get("tx_dbm") or "") if r.get("tx_dbm") is not None else "",
                    str(r.get("status") or "offline"),
                    str(r.get("last_updated") or now),
                )
                for r in batch
                if r.get("ip")
            ],
        )
        db.commit()
    finally:
        db.close()


def get_cached_signal(ip):
    """Return a signal_cache row for an IP, or None."""
    return _fetchone("SELECT * FROM signal_cache WHERE ip = ?", (ip,))


def list_signal_cache():
    """Return all cached signals."""
    return _fetchall("SELECT * FROM signal_cache ORDER BY ip")


# ── Network Links (Phase 13) ────────────────────────────────

def list_network_links():
    """Return all network links (sectors/links), newest first."""
    return _fetchall(
        "SELECT * FROM network_links ORDER BY id DESC"
    )


def get_network_link(link_id):
    """Return a network link row by id, or None."""
    return _fetchone(
        "SELECT * FROM network_links WHERE id = ?", (link_id,)
    )


def add_network_link(name, ip="", link_type="MikroTik", location="", notes="",
                     username="", password="", community="public"):
    """Insert a network link. Returns the new id.

    Args:
        name: device/link name (required).
        ip: management IP address.
        link_type: 'MikroTik' | 'Ubnt' | 'Mimosa'.
        location: physical location.
        notes: free-text notes.
        username: management login username (MikroTik/SNMP).
        password: management login password (MikroTik/SNMP).
        community: SNMP community string (default 'public').
    """
    return _insert(
        "INSERT INTO network_links (name, ip, link_type, location, notes, "
        "username, password, community) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, ip, link_type, location, notes, username, password, community),
    )


def update_network_link(link_id, name=None, ip=None, link_type=None,
                        location=None, notes=None, username=None,
                        password=None, community=None):
    """Update allowed network link fields in one safe statement."""
    updates = {}
    if name is not None:
        updates["name"] = name
    if ip is not None:
        updates["ip"] = ip
    if link_type in ("MikroTik", "Ubnt", "Mimosa"):
        updates["link_type"] = link_type
    if location is not None:
        updates["location"] = location
    if notes is not None:
        updates["notes"] = notes
    if username is not None:
        updates["username"] = username
    if password is not None:
        updates["password"] = password
    if community is not None:
        updates["community"] = community
    if not updates:
        return
    cols = ", ".join(f"{k} = ?" for k in updates)
    _execute(f"UPDATE network_links SET {cols} WHERE id = ?", (*updates.values(), link_id))


def delete_network_link(link_id):
    """Delete a network link by id."""
    _execute("DELETE FROM network_links WHERE id = ?", (link_id,))


# ── Dashboard KPIs ──────────────────────────────────────────

def dashboard_stats(month, year):
    """Aggregate KPIs for the dashboard."""
    row = _fetchone(
        "SELECT "
        "(SELECT COUNT(*) FROM customers WHERE is_active = 1 AND status = 'active') AS active_customers, "
        "(SELECT COUNT(*) FROM packages) AS total_packages, "
        "(SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE month = ? AND year = ?) AS expected_income, "
        "(SELECT COALESCE(SUM(paid_amount), 0) FROM invoices WHERE month = ? AND year = ?) AS collected, "
        "(SELECT COALESCE(SUM(total_amount - paid_amount), 0) FROM invoices WHERE is_paid = 0) AS total_debt",
        (month, year, month, year),
    )
    return {
        "active_customers": row["active_customers"] or 0,
        "total_packages": row["total_packages"] or 0,
        "expected_income": row["expected_income"] or 0,
        "collected": row["collected"] or 0,
        "total_debt": row["total_debt"] or 0,
    }


def customers_expiring_between(start_dt, end_dt):
    """Active customers whose renewal_date falls in [start, end]."""
    return _fetchall(
        _customer_select()
        + "WHERE c.is_active = 1 AND c.status = 'active' AND c.renewal_date IS NOT NULL "
        + "AND c.renewal_date >= ? AND c.renewal_date <= ? "
        + "ORDER BY c.renewal_date ASC",
        (start_dt, end_dt),
    )


def customers_with_debt_or_expired():
    """Customers with unpaid debt OR whose renewal already passed."""
    rows = _fetchall(
        _customer_select()
        + "WHERE c.is_active = 1 "
        + "ORDER BY c.full_name"
    )
    result = []
    for c in rows:
        debt = customer_unpaid_debt(c["id"])
        expired = bool(c["renewal_date"]) and str(c["renewal_date"]) < now_str()
        if debt > 0 or expired:
            result.append(dict(c))
            result[-1]["total_debt"] = debt
            result[-1]["whatsapp_phone"] = c["whatsapp_phone"] or ""
            result[-1]["phone2"] = c["phone2"] or ""
    return result


# ── Export (Excel) helpers ──────────────────────────────────

def export_customers():
    """All customers with package join, ordered by name."""
    return _fetchall(_customer_select() + "ORDER BY c.full_name")


def export_invoices():
    """All invoices joined with customer name."""
    return _fetchall(_invoice_select() + "ORDER BY c.full_name, i.year, i.month")


def export_payments():
    """All payments joined with customer name."""
    return _fetchall(_payment_select() + "ORDER BY pay.payment_date DESC")


if __name__ == "__main__":
    init_db()
    print("[DB] Initialization complete.")