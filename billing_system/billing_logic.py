"""Core billing logic (Phase 3).

process_payment:
  1. Logs the payment in the Payments table.
  2. Extends the subscriber's expiry:
       - if still active  -> old expiry + days_to_add
       - if already passed -> today + days_to_add
  3. Sets status back to 'active'.
"""
from datetime import date, datetime, timedelta

from .database import get_connection, get_subscriber


def _parse_date(value):
    """Parse an ISO date string (YYYY-MM-DD) into a datetime.date."""
    return datetime.strptime(value, "%Y-%m-%d").date()


def process_payment(mikrotik_username, amount, days_to_add, db_path=None):
    """Record a payment and extend the subscriber's expiry date.

    Args:
        mikrotik_username: the subscriber's unique PPP username.
        amount: payment amount in IQD.
        days_to_add: number of days to add to the expiry date.
        db_path: optional override for the database path.

    Returns:
        A dict describing the resulting subscriber and the new expiry.

    Raises:
        ValueError: if no subscriber has that mikrotik_username.
    """
    if days_to_add <= 0:
        raise ValueError("days_to_add must be a positive number of days.")

    subscriber = get_subscriber(mikrotik_username, db_path=db_path)
    if subscriber is None:
        raise ValueError("No subscriber found for mikrotik_username: %s" % mikrotik_username)

    today = date.today()
    current_expiry = _parse_date(subscriber["expiry_date"])

    # If already expired, renew from today; otherwise extend the current expiry.
    base = today if current_expiry < today else current_expiry
    new_expiry = base + timedelta(days=days_to_add)

    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO Payments (subscriber_id, amount, payment_date) VALUES (?, ?, ?)",
            (subscriber["id"], float(amount), today.isoformat()),
        )
        conn.execute(
            "UPDATE Subscribers SET expiry_date = ?, status = 'active' WHERE id = ?",
            (new_expiry.isoformat(), subscriber["id"]),
        )
        conn.commit()
    finally:
        conn.close()

    result = dict(subscriber)
    result["expiry_date"] = new_expiry.isoformat()
    result["status"] = "active"
    result["payment_amount"] = amount
    result["payment_date"] = today.isoformat()
    return result