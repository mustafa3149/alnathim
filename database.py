"""
Database Layer for Al-Nathim SaaS Platform
==========================================
Uses SQLAlchemy — works with PostgreSQL (production/Render) and SQLite (local dev).
Multi-tenant: every business table has owner_id linking to isp_owners.
"""

import logging
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, Boolean, DateTime, Date, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from werkzeug.security import generate_password_hash, check_password_hash

from config import IS_POSTGRES, DATABASE_URL, LOCAL_DB_PATH

log = logging.getLogger(__name__)

# ── Engine & Session ──────────────────────────────────────

if IS_POSTGRES:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
else:
    import os
    os.makedirs(os.path.dirname(LOCAL_DB_PATH) if os.path.dirname(LOCAL_DB_PATH) else ".", exist_ok=True)
    engine = create_engine(f"sqlite:///{LOCAL_DB_PATH}", echo=False)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    """Get a database session (SQLAlchemy Session)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def db_session():
    """Return a new Session (for non-request contexts like scripts)."""
    return SessionLocal()


# ── Models ─────────────────────────────────────────────────

class SuperAdmin(Base):
    """The developer / platform administrator."""
    __tablename__ = "super_admins"

    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class ISPOwner(Base):
    """The ISP owner / app user. Each owner sees ONLY their own data."""
    __tablename__ = "isp_owners"

    id = Column(Integer, primary_key=True)
    full_name = Column(String(120), nullable=False)
    username = Column(String(80), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    account_status = Column(String(20), default="pending")  # pending / active / suspended
    subscription_end_date = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Company profile (from old isp_info table)
    owner_name = Column(String(120), default="")
    company_name = Column(String(120), default="الناظم")
    phone = Column(String(50), default="")
    address = Column(String(200), default="")
    footer_note = Column(Text, default="شكراً لتعاملكم معنا")

    # JSON config (numeral style, default_connection_type, etc.)
    config = Column(JSON, default=dict)

    # Relationships
    customers = relationship("Customer", back_populates="owner", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="owner", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="owner", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="owner", cascade="all, delete-orphan")
    packages = relationship("Package", back_populates="owner", cascade="all, delete-orphan")
    maintenance_tickets = relationship("MaintenanceTicket", back_populates="owner", cascade="all, delete-orphan")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Customer(Base):
    """The ISP subscribers — scoped by owner_id."""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    phone = Column(String(30))
    phone2 = Column(String(30))
    whatsapp_phone = Column(String(30))
    address = Column(String(200))
    region = Column(String(100))
    is_active = Column(Boolean, default=True)
    subscription_status = Column(String(20), default="active")
    subscription_date = Column(DateTime)
    renewal_date = Column(DateTime)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    username = Column(String(80))
    password = Column(String(255))
    ip_address = Column(String(50))
    device_type = Column(String(50))
    package_name = Column(String(50))
    package_price = Column(Integer)
    last_whatsapp_sent = Column(Date)

    owner = relationship("ISPOwner", back_populates="customers")
    invoices = relationship("Invoice", back_populates="customer", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="customer", cascade="all, delete-orphan")
    maintenance_tickets = relationship("MaintenanceTicket", back_populates="customer", cascade="all, delete-orphan")


class Invoice(Base):
    """Monthly bills — scoped by owner_id."""
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    month = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    package_name = Column(String(50))
    package_price = Column(Integer)
    total_amount = Column(Float, nullable=False, default=0)
    paid_amount = Column(Float, default=0)
    is_paid = Column(Boolean, default=False)
    outage_deduction = Column(Float, default=0)
    previous_debt = Column(Float, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (__import__("sqlalchemy").UniqueConstraint("customer_id", "month", "year"),)

    owner = relationship("ISPOwner", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")
    payments = relationship("Payment", back_populates="invoice", cascade="all, delete-orphan")
    extras = relationship("InvoiceExtra", back_populates="invoice", cascade="all, delete-orphan")


class Payment(Base):
    """Payment records — scoped by owner_id."""
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    payment_date = Column(Date, nullable=False)
    payment_method = Column(String(30), default="نقدي")
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("ISPOwner", back_populates="payments")
    invoice = relationship("Invoice", back_populates="payments")
    customer = relationship("Customer", back_populates="payments")


class Expense(Base):
    """Expenses — scoped by owner_id."""
    __tablename__ = "expenses"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    expense_date = Column(Date, nullable=False)
    category = Column(String(50), default="أخرى")
    amount = Column(Float, nullable=False)
    description = Column(Text)
    recipient_name = Column(String(120))
    subscriber_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("ISPOwner", back_populates="expenses")


class Package(Base):
    """ISP packages — scoped by owner_id (each owner has their own pricing)."""
    __tablename__ = "packages"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    name = Column(String(50), nullable=False)
    price = Column(Integer, nullable=False)

    owner = relationship("ISPOwner", back_populates="packages")


class InvoiceExtra(Base):
    """Extra items attached to invoices — scoped by owner_id."""
    __tablename__ = "invoice_extras"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    item_name = Column(String(120), nullable=False)
    item_price = Column(Integer, nullable=False)

    invoice = relationship("Invoice", back_populates="extras")


class MaintenanceTicket(Base):
    """Maintenance requests — scoped by owner_id."""
    __tablename__ = "maintenance_tickets"

    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, ForeignKey("isp_owners.id"), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    issue_description = Column(Text, nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)

    owner = relationship("ISPOwner", back_populates="maintenance_tickets")
    customer = relationship("Customer", back_populates="maintenance_tickets")


# ── Init / Migration ───────────────────────────────────────

def init_db():
    """Create all tables and seed default superadmin."""
    Base.metadata.create_all(bind=engine)
    log.info("[DB] Tables created/verified.")

    # ── Migration: add owner_id to existing tables (legacy single-user DB) ──
    _migrate_existing_db()

    # Seed default superadmin if none exists
    db = SessionLocal()
    try:
        existing = db.query(SuperAdmin).filter_by(username="admin").first()
        if not existing:
            from config import SUPERADMIN_USERNAME, SUPERADMIN_PASSWORD
            admin = SuperAdmin(username=SUPERADMIN_USERNAME)
            admin.set_password(SUPERADMIN_PASSWORD)
            db.add(admin)
            db.commit()
            log.info(f"[DB] Seeded default superadmin: {SUPERADMIN_USERNAME}")
    except Exception as e:
        log.error(f"[DB] Seed superadmin failed: {e}")
        db.rollback()
    finally:
        db.close()


def _migrate_existing_db():
    """
    Migration for legacy SQLite databases.
    Adds owner_id columns to existing business tables if they don't exist.
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        tables = ["customers", "invoices", "payments", "expenses", "packages",
                  "invoice_extras", "maintenance_tickets"]

        with engine.connect() as conn:
            for table in tables:
                if table in inspector.get_table_names():
                    try:
                        cols = [c["name"] for c in inspector.get_columns(table)]
                        if "owner_id" not in cols:
                            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER DEFAULT 1"))
                            log.info(f"[DB] Migration: added owner_id to {table}")
                    except Exception as e:
                        log.warning(f"[DB] Migration warning for {table}: {e}")
            conn.commit()
    except Exception as e:
        log.error(f"[DB] Migration failed: {e}")


def seed_default_owner():
    """Create a default owner (id=1) for backward compatibility / local testing."""
    db = SessionLocal()
    try:
        owner = db.query(ISPOwner).filter_by(username="demo").first()
        if not owner:
            owner = ISPOwner(
                full_name="المستخدم التجريبي",
                username="demo",
                account_status="active",
            )
            owner.set_password("demo123")
            db.add(owner)
            db.commit()

        # Seed default packages for this owner
        pkg_count = db.query(Package).filter_by(owner_id=owner.id).count()
        if pkg_count == 0:
            defaults = [
                ("Economy", 35000), ("Plus", 45000), ("Standard", 50000),
                ("Turbo", 65000), ("More", 75000), ("Business", 100000),
            ]
            for name, price in defaults:
                db.add(Package(owner_id=owner.id, name=name, price=price))
            db.commit()
            log.info("[DB] Seeded default packages for demo owner.")

        return owner.id
    except Exception as e:
        log.error(f"[DB] Seed default owner failed: {e}")
        db.rollback()
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
    print("[DB] Initialization complete.")