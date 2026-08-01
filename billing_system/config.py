"""Load billing system settings from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def load_billing_config():
    """Return dict with billing database path from env variables."""
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "isp_billing.db")
    return {
        "db_path": os.getenv("BILLING_DB_PATH", default_path).strip(),
    }