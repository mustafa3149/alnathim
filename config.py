"""
Central Configuration for the Al-Nathim SaaS Platform
=====================================================
Reads configuration from environment variables (.env file).
- DATABASE_URL: PostgreSQL URL for production (Supabase/Render), defaults to SQLite locally
- SECRET_KEY  : Flask session encryption key (MUST be random in production)
- SUPERADMIN_USERNAME / SUPERADMIN_PASSWORD: default admin credentials
"""

import os
from dotenv import load_dotenv

# Load .env if present (local development; Render uses real env vars)
load_dotenv()


def _clean(value, default=""):
    """Trim whitespace and surrounding quotes from an env value."""
    if value is None:
        return default
    return str(value).strip().strip('"\'')


# ── Database ──────────────────────────────────────────────
# Example: postgresql://user:pass@host:5432/dbname
DATABASE_URL = _clean(os.getenv("DATABASE_URL"), "")

# Local fallback paths (Windows AppData)
APP_NAME = "AlNathim"
LOCAL_DB_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
LOCAL_DB_PATH = os.path.join(LOCAL_DB_DIR, "mawlidati.db")

IS_POSTGRES = bool(DATABASE_URL) and not DATABASE_URL.lower().startswith("sqlite")

# ── Flask ─────────────────────────────────────────────────
DEFAULT_SECRET_KEY = "al-nathim-dev-secret-key-change-in-production"
SECRET_KEY = _clean(os.getenv("SECRET_KEY"), DEFAULT_SECRET_KEY)

# ── SuperAdmin (first-run seed) ───────────────────────────
DEFAULT_SUPERADMIN_PASSWORD = "admin123"
SUPERADMIN_USERNAME = _clean(os.getenv("SUPERADMIN_USERNAME"), "admin")
SUPERADMIN_PASSWORD = _clean(os.getenv("SUPERADMIN_PASSWORD"), DEFAULT_SUPERADMIN_PASSWORD)

# ── Production Safety Guards ──────────────────────────────
# Refuse to boot with publicly-known credentials when connected to a
# real (PostgreSQL) database. Prevents deploying to Render/Supabase
# with a forgeable SECRET_KEY or an exposed admin password.


def _assert_secure():
    """Raise RuntimeError if known-default secrets are used with Postgres."""
    if not IS_POSTGRES:
        return  # local SQLite development is fine with defaults

    problems = []
    if SECRET_KEY == DEFAULT_SECRET_KEY:
        problems.append("SECRET_KEY is still the publicly-known default key.")
    if SUPERADMIN_PASSWORD == DEFAULT_SUPERADMIN_PASSWORD:
        problems.append("SUPERADMIN_PASSWORD is still the default 'admin123'.")

    if problems:
        msg = (
            "\n"
            "──────────────────────────────────────────────────────────────\n"
            "⚠️  تنبيه أمني — رفض تشغيل التطبيق بقيم افتراضية معروفة!\n"
            "──────────────────────────────────────────────────────────────\n"
            "الإعداد الحالي يستخدم PostgreSQL (إنتاج) لكنه ما زال يحمل قيماً\n"
            "افتراضية معلنة للعامة. هذا يسمح لأي شخص بتزوير جلسات الدخول\n"
            "أو الدخول للوحة المدير.\n\n"
            "الرجاء ضبط المتغيرات التالية في ملف .env أو في إعدادات Render:\n"
        )
        for p in problems:
            msg += f"   - {p}\n"
        msg += (
            "\nقم بإنشاء SECRET_KEY عشوائي:\n"
            "   python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "──────────────────────────────────────────────────────────────\n"
        )
        raise RuntimeError(msg)


_assert_secure()