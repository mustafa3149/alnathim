"""Central Configuration for the Al-Nathim SaaS Platform.

Reads configuration from environment variables (.env file).
- SQLITE_PATH : persistent SQLite file path (Render disk / production)
- SECRET_KEY  : Flask session encryption key (MUST be random in production)
- SUPERADMIN_USERNAME / SUPERADMIN_PASSWORD: default admin credentials.
"""

import os
import secrets
import sys
from dotenv import load_dotenv

# Load .env if present (local development; Render uses real env vars)

# PyInstaller onefile EXE: the bundled .env is extracted to sys._MEIPASS, a
# temp folder that a bare load_dotenv() never searches (it walks the CWD and
# the caller file's directory). Without RELAY_URL the desktop EXE silently
# stops forwarding registrations/logins to the cloud admin — accounts stay
# stuck in the local DB while the admin watches an empty request queue on the
# Render website. Load the bundled copy (and any .env placed next to the EXE)
# explicitly. override=False keeps real env vars (Render) authoritative.


def _load_env_file(path):
    """Load a .env file when present without overriding existing env vars.

    Args:
        path: absolute path to a .env file (may not exist).

    Returns:
        None.
    """
    if path and os.path.isfile(path):
        load_dotenv(path, override=False)


if getattr(sys, "frozen", False):
    _load_env_file(os.path.join(getattr(sys, "_MEIPASS", ""), ".env"))
    _load_env_file(os.path.join(os.path.dirname(os.path.abspath(sys.executable)), ".env"))


load_dotenv()


def _clean(value, default=""):
    """Trim whitespace and surrounding quotes from an env value."""
    if value is None:
        return default
    return str(value).strip().strip('"\'')


# ── Database ──────────────────────────────────────────────
# SQLite is the database engine (parameterized queries throughout).
# Production (Render): attach a persistent Disk and set SQLITE_PATH to a path
# on that disk (e.g. /var/data/mawlidati.db) so data survives redeploys.
DATABASE_URL = _clean(os.getenv("DATABASE_URL"), "")

# Local fallback paths (Windows AppData)
APP_NAME = "AlNathim"
LOCAL_DB_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)

# SQLITE_PATH overrides the DB location (Render persistent disk, staging, etc.).
SQLITE_PATH = _clean(os.getenv("SQLITE_PATH"), "")
LOCAL_DB_PATH = SQLITE_PATH or os.path.join(LOCAL_DB_DIR, "mawlidati.db")

# ── Turso (hosted libSQL) — free-tier cloud durability ─────────
# When BOTH variables are present the main database connects to the
# hosted Turso database instead of the local SQLite file (survives every
# Render restart / redeploy for free). Set them in Render's environment
# (cloud) or in .env (desktop EXE) to use Turso; leave empty to stay on
# the local SQLite file — the desktop launcher keeps working offline.
TURSO_DATABASE_URL = _clean(os.getenv("TURSO_DATABASE_URL"), "")
TURSO_AUTH_TOKEN = _clean(os.getenv("TURSO_AUTH_TOKEN"), "")
USE_TURSO = bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)


IS_POSTGRES = False  # This build is SQLite-only; DATABASE_URL is not used.
# True when hosted on Render (Render sets RENDER=true automatically in every
# service env). The local desktop EXE does NOT have it → IS_RENDER=False →
# registration requests are forwarded to RELAY_URL so the admin sees them
# on the cloud immediately.
IS_RENDER = os.getenv("RENDER", "").strip().lower() in ("1", "true", "yes", "on")

# ── Flask ─────────────────────────────────────────────────
# SECRET_KEY:
#   1) Environment override (Render) wins — set it there to a random value.
#   2) Otherwise (local EXE/desktop) we generate a random key ONCE and persist
#      it in %APPDATA%\AlNathim\secret.key. This guarantees:
#        - sessions survive restarts (key stays stable),
#        - every installation has its own unique key (no shared/public key,
#          no forged/leftover admin sessions from another device/tester).
_DEFAULT_SECRET_KEY = "al-nathim-dev-secret-key-change-in-production"
_env_secret = _clean(os.getenv("SECRET_KEY"), "")
if _env_secret:
    SECRET_KEY = _env_secret
else:
    _secret_file = os.path.join(LOCAL_DB_DIR, "secret.key")
    if os.path.exists(_secret_file):
        try:
            with open(_secret_file, "r", encoding="utf-8") as _sf:
                _stored = _sf.read().strip()
            SECRET_KEY = _stored if _stored else _DEFAULT_SECRET_KEY
        except OSError:
            SECRET_KEY = _DEFAULT_SECRET_KEY
    else:
        SECRET_KEY = secrets.token_hex(32)
        try:
            os.makedirs(LOCAL_DB_DIR, exist_ok=True)
            with open(_secret_file, "w", encoding="utf-8") as _sf:
                _sf.write(SECRET_KEY)
        except OSError:
            pass
del _env_secret

# ── SuperAdmin (first-run seed) ───────────────────────────
DEFAULT_SUPERADMIN_PASSWORD = "admin123"
SUPERADMIN_USERNAME = _clean(os.getenv("SUPERADMIN_USERNAME"), "admin")
SUPERADMIN_PASSWORD = _clean(os.getenv("SUPERADMIN_PASSWORD"), DEFAULT_SUPERADMIN_PASSWORD)

# ── Registration & Sessions (Phase 14.4) ──────────────────
# When "false", self-registration is disabled entirely (admin creates accounts).
# When "true", open registration creates pending accounts; invite codes activate instantly.
ALLOW_OPEN_REGISTRATION = os.getenv("ALLOW_OPEN_REGISTRATION", "true").strip().lower() in ("1", "true", "yes", "on")
SESSION_LIFETIME_MINUTES = int(os.getenv("SESSION_LIFETIME_MINUTES", "480") or 480)

# ── Signal Agent (LAN scanner → Render relay, Phase 14.6) ──
# AGENT_TOKEN must match between the tower-PC scanner and the server when
# relaying batched signal snapshots. Missing/invalid → 401.
AGENT_TOKEN = _clean(os.getenv("AGENT_TOKEN"), "change-me-agent-token")
# How often the LAN scanner polls all subscribers (minutes).
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "3") or 3)
# Concurrent SNMP workers in the scanner (keep modest to protect the MikroTik).
SCAN_THREADS = int(os.getenv("SCAN_THREADS", "8") or 8)
# Where the LAN agent relays batches (empty = disable relay; local SQLite cache still works).
RELAY_URL = _clean(os.getenv("RELAY_URL"), "")

# ── Desktop PC Activation (Phase 14.7: HWID keys) ───────────
# HMAC-SHA256 signing secret for desktop activation keys. Must be identical
# on the cloud (Render) and the local EXE so a key issued on the website can
# be validated by the desktop app. Falls back to SECRET_KEY when unset.
ACTIVATION_KEY_SECRET = _clean(os.getenv("ACTIVATION_KEY_SECRET"), SECRET_KEY)

# ── Mobile API (Phase 2: native Android app) ────────────────
# HMAC-signed bearer tokens for /api/mobile/v1 (native app).
# Access-token TTL in hours; refresh-token TTL in days.
AUTH_TOKEN_TTL_HOURS = float(os.getenv("AUTH_TOKEN_TTL_HOURS", "24") or 24)
AUTH_REFRESH_TTL_DAYS = float(os.getenv("AUTH_REFRESH_TTL_DAYS", "30") or 30)

# ── Security Hardening (Phase 14.5) ────────────────────────
# Failed-login lockout: after MAX_FAILED_LOGINS wrong attempts the account
# auto-suspends until an admin reactivates it.
MAX_FAILED_LOGINS = int(os.getenv("MAX_FAILED_LOGINS", "5") or 5)
# Per-IP brute-force rate limit on /login + /register:
# LOGIN_RATE_LIMIT_ATTEMPTS tries every LOGIN_RATE_LIMIT_WINDOW_MINUTES.
LOGIN_RATE_LIMIT_ATTEMPTS = int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "10") or 10)
LOGIN_RATE_LIMIT_WINDOW_MINUTES = int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_MINUTES", "5") or 5)
# Send Set-Cookie only over HTTPS when true. Auto-true in production
# (Render uses HTTPS) or when COOKIE_SECURE is explicitly set.
_COOKIE_SECURE_DEFAULT = "true" if (SQLITE_PATH or DATABASE_URL) else "false"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", _COOKIE_SECURE_DEFAULT).strip().lower() in ("1", "true", "yes", "on")

# ── Production Safety Guards ──────────────────────────────
# In a SQLite-only build the app is safe with local defaults, so this guard is
# only relevant if a future build ever connects to a remote database.


def _assert_secure():
    """Raise RuntimeError if known-default secrets are exposed in production."""
    if not IS_POSTGRES:
        return  # local/SQLite build is fine with defaults

    problems = []
    if SECRET_KEY == _DEFAULT_SECRET_KEY:
        problems.append("SECRET_KEY is still the publicly-known default key.")
    if SUPERADMIN_PASSWORD == DEFAULT_SUPERADMIN_PASSWORD:
        problems.append("SUPERADMIN_PASSWORD is still the default 'admin123'.")

    if problems:
        msg = (
            "\n"
            "──────────────────────────────────────────────────────────────\n"
            "⚠️  تنبيه أمني — رفض تشغيل التطبيق بقيم افتراضية معروفة!\n"
            "──────────────────────────────────────────────────────────────\n"
            "الإعداد الحالي يستخدم قاعدة بيانات إنتاجية لكنه ما زال يحمل قيماً\n"
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