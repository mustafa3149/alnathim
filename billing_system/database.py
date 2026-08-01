"""SQLite database layer for the Billing System (Phase 3).

Uses Python's built-in sqlite3 — no external ORM.
Dates are stored as ISO strings (YYYY-MM-DD) and converted to/from
datetime.date at the application layer.
"""
import os
import sqlite3
from datetime import date, datetime

from .config import load_billing_config

DEFAULT_DB_PATH = load_billing_config()["db_path"]


def get_connection(db_path=None):
    """Open a sqlite3 connection with Row factory and foreign keys on."""
    path = db_path if db_path is not None else DEFAULT_DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path=None):
    """Create the Subscribers and Payments tables if they don't exist."""
    conn = get_connection(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS Subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mikrotik_username TEXT NOT NULL UNIQUE,
                monthly_fee REAL NOT NULL DEFAULT 0,
                expiry_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active'
            );

            CREATE TABLE IF NOT EXISTS Payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                payment_date TEXT NOT NULL,
                FOREIGN KEY (subscriber_id) REFERENCES Subscribers (id)
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row) if row is not None else None


def add_subscriber(name, mikrotik_username, monthly_fee, expiry_date, status="active", db_path=None):
    """Insert a new subscriber.

    Args:
        name: subscriber display name.
        mikrotik_username: unique PPP username on the router.
        monthly_fee: monthly fee in IQD.
        expiry_date: a datetime.date (or ISO date string).
        status: 'active' or 'expired'.
        db_path: optional override for the database path.

    Returns:
        The new subscriber id.

    Raises:
        sqlite3.IntegrityError: if the mikrotik_username already exists.
    """
    if isinstance(expiry_date, date) and not isinstance(expiry_date, datetime):
        expiry_iso = expiry_date.isoformat()
    elif isinstance(expiry_date, datetime):
        expiry_iso = expiry_date.date().isoformat()
    else:
        expiry_iso = str(expiry_date)

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO Subscribers (name, mikrotik_username, monthly_fee, expiry_date, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, mikrotik_username, float(monthly_fee), expiry_iso, status),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_subscriber(mikrotik_username, db_path=None):
    """Return a subscriber dict by mikrotik_username, or None."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, mikrotik_username, monthly_fee, expiry_date, status "
            "FROM Subscribers WHERE mikrotik_username = ?",
            (mikrotik_username,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_subscriber_by_id(subscriber_id, db_path=None):
    """Return a subscriber dict by database id, or None."""
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, mikrotik_username, monthly_fee, expiry_date, status "
            "FROM Subscribers WHERE id = ?",
            (subscriber_id,),
        ).fetchone()
        return _row_to_dict(row)
    finally:
        conn.close()


def get_all_subscribers(db_path=None):
    """Return every subscriber as a list of dicts."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, name, mikrotik_username, monthly_fee, expiry_date, status "
            "FROM Subscribers ORDER BY id"
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_subscriber(subscriber_id, db_path=None, **fields):
    """Update only the provided fields of a subscriber.

    Args:
        subscriber_id: the subscriber's database id.
        db_path: optional override for the database path.
        **fields: any of name, mikrotik_username, monthly_fee,
                  expiry_date (date/ISO string), status.

    Returns:
        The updated subscriber dict, or None if the id does not exist.
    """
    allowed = {"name", "mikrotik_username", "monthly_fee", "expiry_date", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_subscriber_by_id(subscriber_id, db_path=db_path)

    # Normalize date/datetime values to ISO date strings.
    if "expiry_date" in updates:
        ed = updates["expiry_date"]
        if isinstance(ed, date) and not isinstance(ed, datetime):
            updates["expiry_date"] = ed.isoformat()
        elif isinstance(ed, datetime):
            updates["expiry_date"] = ed.date().isoformat()
        else:
            updates["expiry_date"] = str(ed)

    cols = ", ".join("%s = ?" % key for key in updates)
    values = list(updates.values()) + [subscriber_id]

    conn = get_connection(db_path)
    try:
        cur = conn.execute("UPDATE Subscribers SET %s WHERE id = ?" % cols, values)
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    return get_subscriber_by_id(subscriber_id, db_path=db_path)


def delete_subscriber(subscriber_id, db_path=None):
    """Delete a subscriber and all of their payment records.

    Args:
        subscriber_id: the subscriber's database id.
        db_path: optional override for the database path.

    Returns:
        True if a row was deleted, False if the id did not exist.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM Payments WHERE subscriber_id = ?", (subscriber_id,))
        cur = conn.execute("DELETE FROM Subscribers WHERE id = ?", (subscriber_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_payment_history(subscriber_id, db_path=None):
    """Return payment records for a subscriber ordered newest-first.

    Args:
        subscriber_id: the subscriber's database id.
        db_path: optional override for the database path.

    Returns:
        List of dicts: {id, amount, payment_date}.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, amount, payment_date FROM Payments "
            "WHERE subscriber_id = ? ORDER BY id DESC",
            (subscriber_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()
