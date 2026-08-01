"""Load MikroTik connection settings from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def load_mikrotik_config():
    """Return dict with host/user/password/port from env variables."""
    return {
        "host": os.getenv("MIKROTIK_HOST", "").strip(),
        "username": os.getenv("MIKROTIK_USER", "admin").strip(),
        "password": os.getenv("MIKROTIK_PASSWORD", ""),
        "port": int(os.getenv("MIKROTIK_PORT", "8728") or 8728),
    }