"""FastAPI REST gateway — exposes Phase 1/2/3 modules as clean JSON APIs.

Endpoints:
  GET  /api/users          merged subscribers + live MikroTik online state
  GET  /api/signal/{ip}    live SNMP signal reading for a client IP
  POST /api/pay            process a payment, then sync to re-enable the user
  POST /api/sync           manually run the full sync engine
"""
import csv
import io
import json
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_API_DIR)
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

_FRONTEND_DIR = os.path.join(_ROOT_DIR, "frontend")
_STATIC_DIR = os.path.join(_ROOT_DIR, "static")
_TOWER_CONFIG_PATH = os.path.join(_API_DIR, "tower_config.json")

from billing_system import get_all_subscribers, init_db, process_payment  # noqa: E402
from billing_system.database import (  # noqa: E402
    add_subscriber,
    delete_subscriber,
    get_payment_history,
    get_subscriber_by_id,
    update_subscriber,
)
from billing_system.sync_engine import _build_mikrotik_manager, sync_mikrotik_status  # noqa: E402

log = logging.getLogger("api_gateway")
logging.basicConfig(level=logging.INFO)

# ── Authentication (Phase 8) ────────────────────────────
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")
if ADMIN_PASSWORD == "1234":
    log.warning("ADMIN_PASSWORD is the default '1234' — change it before any real deployment.")

try:
    AUTH_TOKEN_TTL_HOURS = float(os.getenv("AUTH_TOKEN_TTL_HOURS", "24"))
except ValueError:
    AUTH_TOKEN_TTL_HOURS = 24.0

_auth_token = secrets.token_hex(32)
_auth_issued_at = datetime.now()


def _token_expired():
    """Return True when the current auth token is past its TTL."""
    return datetime.now() - _auth_issued_at > timedelta(hours=AUTH_TOKEN_TTL_HOURS)


def _issue_token():
    """Generate a fresh auth token and record its issue time."""
    global _auth_token, _auth_issued_at
    _auth_token = secrets.token_hex(32)
    _auth_issued_at = datetime.now()


def require_auth(authorization: str = Header(None)):
    """FastAPI dependency: raise 401 unless a valid Bearer token is present."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "يرجى تسجيل الدخول."},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token or token != _auth_token or _token_expired():
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "الجلسة منتهية."},
        )
    return token

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Ensure the Phase 3 tables exist before serving requests."""
    init_db()
    log.info("[API] Database initialized.")
    yield


app = FastAPI(
    title="Al-Nathim API Gateway",
    description="REST gateway for subscribers, payments, MikroTik sync and SNMP signals.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS: permissive for development; restrict origins in production ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Static / frontend serving ─────────────────────────────
# Phase 5 PWA: serve icons/manifest assets on /static.
# NOTE: the root "/" mount for the UI is registered at the END of this
# module (after all /api routes) so it never shadows the API.
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ── Pydantic request/response models ────────────────────────────────
class PayRequest(BaseModel):
    """Validated payload for POST /api/pay."""

    mikrotik_username: str = Field(..., min_length=1, description="PPP username on the router")
    amount: float = Field(..., gt=0, description="Payment amount in IQD")
    days_to_add: int = Field(..., gt=0, description="Days to extend the subscription")


class UserPayload(BaseModel):
    """Validated payload for POST /api/users (add subscriber)."""

    name: str = Field(..., min_length=1, description="Subscriber display name")
    mikrotik_username: str = Field(..., min_length=1, description="Unique PPP username")
    monthly_fee: float = Field(..., gt=0, description="Monthly fee in IQD")
    expiry_date: str = Field(..., min_length=1, description="Expiry date YYYY-MM-DD")


class UserUpdate(BaseModel):
    """Validated payload for PUT /api/users/{id} (optional fields)."""

    name: str = Field(None, min_length=1)
    mikrotik_username: str = Field(None, min_length=1)
    monthly_fee: float = Field(None, gt=0)
    expiry_date: str = Field(None, min_length=1)


class LoginRequest(BaseModel):
    """Validated payload for POST /api/login."""

    password: str = Field(..., min_length=1, description="Admin PIN/password")


# ── Helpers ─────────────────────────────────────────────────────────
def _get_connected_manager():
    """Return a connected MikroTikManager, raising HTTPException on failure."""
    try:
        return _build_mikrotik_manager()
    except Exception as e:  # noqa: BLE001
        log.error("[API] MikroTik connection failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail={"error": "mikrotik_unreachable", "message": "Cannot reach the MikroTik router."},
        ) from e


def _active_sessions_map():
    """Fetch active PPP sessions; return (map, error) — empty map on failure."""
    try:
        manager = _build_mikrotik_manager()
        try:
            clients = manager.get_active_clients_with_ip()
        finally:
            manager.disconnect()
        by_user = {}
        for c in clients:
            key = c.get("user") or c.get("name") or ""
            if key:
                by_user.setdefault(key, c)
        return by_user, None
    except Exception as e:  # noqa: BLE001
        log.error("[API] Active clients fetch failed: %s", e)
        return {}, e


# ── Endpoints ────────────────────────────────────────────────────────
@app.post("/api/login")
def login(payload: LoginRequest):
    """Verify the admin password and return a fresh bearer token."""
    if not secrets.compare_digest(payload.password, ADMIN_PASSWORD):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid_password", "message": "كلمة المرور غير صحيحة."},
        )
    _issue_token()
    return {"token": _auth_token, "expires_in": int(AUTH_TOKEN_TTL_HOURS * 3600)}


@app.get("/api/users", dependencies=[Depends(require_auth)])
def get_users():
    """Return subscribers merged with live online state from MikroTik.

    If the router is unreachable the clients are shown as offline rather
    than failing the whole request.
    """
    try:
        subscribers = get_all_subscribers()
    except Exception as e:  # noqa: BLE001
        log.error("[API] Subscriber read failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "database_error", "message": "Failed to read subscribers."},
        ) from e

    active_map, router_error = _active_sessions_map()
    if router_error is not None:
        log.warning("[API] Router unreachable — returning subscribers as offline.")

    result = []
    for sub in subscribers:
        session = active_map.get(sub["mikrotik_username"])
        is_online = session is not None
        result.append(
            {
                "id": sub["id"],
                "name": sub["name"],
                "mikrotik_username": sub["mikrotik_username"],
                "status": sub["status"],
                "balance": sub["monthly_fee"],
                "expiry_date": sub["expiry_date"],
                "is_online": is_online,
                "ip_address": session.get("address") if session else None,
            }
        )
    return result


@app.post("/api/users", dependencies=[Depends(require_auth)])
def create_user(payload: UserPayload):
    """Add a new subscriber to the billing DB."""
    try:
        subscriber_id = add_subscriber(
            name=payload.name.strip(),
            mikrotik_username=payload.mikrotik_username.strip(),
            monthly_fee=payload.monthly_fee,
            expiry_date=payload.expiry_date,
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "unique" in msg or "integrity" in msg:
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_username", "message": "اسم المستخدم موجود مسبقاً."},
            ) from e
        log.error("[API] Add subscriber failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "database_error", "message": "فشل إضافة المشترك."},
        ) from e
    return get_subscriber_by_id(subscriber_id) or {"id": subscriber_id}


@app.put("/api/users/{subscriber_id}", dependencies=[Depends(require_auth)])
def edit_user(subscriber_id: int, payload: UserUpdate):
    """Update an existing subscriber's details (only provided fields)."""
    fields = {}
    if payload.name is not None:
        fields["name"] = payload.name.strip()
    if payload.mikrotik_username is not None:
        fields["mikrotik_username"] = payload.mikrotik_username.strip()
    if payload.monthly_fee is not None:
        fields["monthly_fee"] = payload.monthly_fee
    if payload.expiry_date is not None:
        fields["expiry_date"] = payload.expiry_date

    try:
        updated = update_subscriber(subscriber_id, **fields)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "unique" in msg or "integrity" in msg:
            raise HTTPException(
                status_code=409,
                detail={"error": "duplicate_username", "message": "اسم المستخدم موجود مسبقاً."},
            ) from e
        log.error("[API] Edit subscriber failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "database_error", "message": "فشل تعديل المشترك."},
        ) from e
    if updated is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "subscriber_not_found", "message": "المشترك غير موجود."},
        )
    return updated


@app.get("/api/users/{subscriber_id}/history", dependencies=[Depends(require_auth)])
def get_user_history(subscriber_id: int):
    """Return the payment history for a subscriber."""
    if get_subscriber_by_id(subscriber_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "subscriber_not_found", "message": "المشترك غير موجود."},
        )
    return get_payment_history(subscriber_id)


@app.delete("/api/users/{subscriber_id}", dependencies=[Depends(require_auth)])
def delete_user(subscriber_id: int):
    """Delete a subscriber (and their payments), disabling them on MikroTik.

    The MikroTik disable is best-effort: if the router is unreachable the
    subscriber is still deleted so the deletion is never blocked by an outage.
    """
    sub = get_subscriber_by_id(subscriber_id)
    if sub is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "subscriber_not_found", "message": "المشترك غير موجود."},
        )

    disabled = False
    try:
        manager = _build_mikrotik_manager()
        try:
            disabled = manager.set_ppp_secret_disabled(sub["mikrotik_username"], True)
            manager.disconnect_ppp_active(sub["mikrotik_username"])
        finally:
            manager.disconnect()
    except Exception as e:  # noqa: BLE001
        log.warning("[API] MikroTik disable failed during delete: %s", e)

    if not delete_subscriber(subscriber_id):
        raise HTTPException(
            status_code=404,
            detail={"error": "subscriber_not_found", "message": "المشترك غير موجود."},
        )
    return {"ok": True, "deleted": True, "disabled_on_mikrotik": disabled}


@app.get("/api/signal/{ip}", dependencies=[Depends(require_auth)])
def get_signal(ip: str, community: str = "public"):
    """Return the live SNMP signal dict for a client IP.

    Args:
        ip: target host.
        community: optional SNMP community override (default 'public');
            passed to the SignalMonitor instead of relying on the .env default.

    SignalMonitor never raises — a missing/timeout device returns
    status offline/timeout, which is forwarded as-is.
    """
    from snmp_monitor.signal_monitor import SignalMonitor

    try:
        monitor = SignalMonitor()
    except Exception as e:  # noqa: BLE001
        log.error("[API] SNMP init failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "snmp_config_error", "message": "SNMP monitor failed to initialize."},
        ) from e

    try:
        data = monitor.get_client_signal(ip, community=community)
    except Exception as e:  # noqa: BLE001
        log.error("[API] Signal fetch failed for %s: %s", ip, e)
        raise HTTPException(
            status_code=500,
            detail={"error": "signal_fetch_error", "message": "Failed to read signal for %s." % ip},
        ) from e

    return data


@app.post("/api/pay", dependencies=[Depends(require_auth)])
def pay(payload: PayRequest):
    """Record a payment, extend expiry, then immediately sync to enable.

    The payment always succeeds even if the router is down — the sync
    summary reports any router errors so the money is never lost.
    """
    try:
        payment = process_payment(
            payload.mikrotik_username,
            amount=payload.amount,
            days_to_add=payload.days_to_add,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "subscriber_not_found", "message": str(e)},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.error("[API] Payment failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "payment_failed", "message": "Failed to record the payment."},
        ) from e

    # Immediate sync so a previously-expired user is re-enabled on /ppp secret.
    manager = None
    sync_summary = {}
    try:
        manager = _build_mikrotik_manager()
        sync_summary = sync_mikrotik_status(mikrotik=manager)
    except Exception as e:  # noqa: BLE001
        log.error("[API] Post-payment sync failed: %s", e)
        sync_summary = {
            "checked": 0,
            "expired": 0,
            "active": 0,
            "disabled": 0,
            "enabled": 0,
            "missing": 0,
            "errors": [{"username": payload.mikrotik_username, "error": str(e)}],
        }
    finally:
        if manager is not None:
            try:
                manager.disconnect()
            except Exception:  # noqa: BLE001
                pass

    return {"payment": payment, "sync": sync_summary}


def _mask(value):
    """Return a masked placeholder for a non-empty secret."""
    return "********" if value else ""


def _load_tower_config():
    """Load saved tower config from disk (empty dict if none)."""
    if os.path.exists(_TOWER_CONFIG_PATH):
        try:
            with open(_TOWER_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_tower_config(cfg):
    """Persist tower config to disk."""
    with open(_TOWER_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@app.get("/api/tower-config", dependencies=[Depends(require_auth)])
def get_tower_config():
    """Return MikroTik + OLT/SNMP settings (password masked).

    Merges the saved config over environment defaults so the form shows
    the effective values even before anything has been saved.
    """
    saved = _load_tower_config()

    # Environment defaults (Phase 1/2 modules)
    mikrotik_default_port = 8728
    try:
        mikrotik_default_port = int(os.getenv("MIKROTIK_PORT", "8728") or 8728)
    except ValueError:
        pass

    snmp_default_port = 161
    try:
        snmp_default_port = int(os.getenv("SNMP_PORT", "161") or 161)
    except ValueError:
        pass

    host = saved.get("mikrotik_host", "").strip() or os.getenv("MIKROTIK_HOST", "").strip()
    result = {
        "mikrotik_host": host,
        "mikrotik_user": saved.get("mikrotik_user", "").strip() or os.getenv("MIKROTIK_USER", "admin").strip(),
        "mikrotik_port": saved.get("mikrotik_port") or mikrotik_default_port,
        "mikrotik_password_set": bool(saved.get("mikrotik_password")),
        "olt_community": saved.get("olt_community", "").strip() or os.getenv("SNMP_COMMUNITY", "public").strip(),
        "olt_port": saved.get("olt_port") or snmp_default_port,
    }
    return result


@app.post("/api/tower-config", dependencies=[Depends(require_auth)])
def save_tower_config(payload: dict):
    """Validate and persist MikroTik + OLT/SNMP settings."""
    mikrotik_host = str(payload.get("mikrotik_host", "")).strip()
    mikrotik_user = str(payload.get("mikrotik_user", "")).strip()
    if not mikrotik_host or not mikrotik_user:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_fields", "message": "مطلوب IP الراوتر واسم المستخدم."},
        )

    try:
        mikrotik_port = int(payload.get("mikrotik_port") or 8728)
        olt_port = int(payload.get("olt_port") or 161)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_port", "message": "المنفذ يجب أن يكون رقماً."},
        ) from None

    saved = _load_tower_config()
    saved["mikrotik_host"] = mikrotik_host
    saved["mikrotik_user"] = mikrotik_user
    saved["mikrotik_port"] = mikrotik_port
    saved["olt_community"] = str(payload.get("olt_community", "")).strip() or "public"
    saved["olt_port"] = olt_port

    # Only overwrite the stored password when a new non-empty one is given.
    new_password = str(payload.get("mikrotik_password", "")).strip()
    if new_password:
        saved["mikrotik_password"] = new_password

    _save_tower_config(saved)
    return {
        "ok": True,
        "mikrotik_host": saved["mikrotik_host"],
        "mikrotik_user": saved["mikrotik_user"],
        "mikrotik_port": saved["mikrotik_port"],
        "olt_community": saved["olt_community"],
        "olt_port": saved["olt_port"],
    }


@app.post("/api/tower-test", dependencies=[Depends(require_auth)])
def test_tower_connection(payload: dict):
    """Try a real RouterOS API login with the submitted credentials."""
    host = str(payload.get("mikrotik_host", "")).strip()
    username = str(payload.get("mikrotik_user", "")).strip()
    password = str(payload.get("mikrotik_password", "")).strip()
    try:
        port = int(payload.get("mikrotik_port") or 8728)
    except (TypeError, ValueError):
        port = 8728

    if not host or not username:
        raise HTTPException(
            status_code=422,
            detail={"error": "missing_fields", "message": "مطلوب IP الراوتر واسم المستخدم."},
        )

    try:
        import routeros_api  # noqa: WPS433

        pool = routeros_api.RouterOsApiPool(
            host,
            username=username,
            password=password,
            port=port,
            plaintext_login=True,
            connect_timeout=8,
        )
        api = pool.login()
        try:
            api.get_resource("/system/identity").get()
        finally:
            try:
                pool.disconnect()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "message": "تم الاتصال بالراوتر بنجاح."}
    except Exception as e:  # noqa: BLE001
        log.error("[API] Tower test failed: %s", e)
        return {"ok": False, "message": "فشل الاتصال: %s" % e}


@app.get("/api/finance/export", dependencies=[Depends(require_auth)])
def export_finance_csv():
    """Return a CSV download of all subscribers, status, and debts.

    Uses UTF-8 with BOM so Arabic opens correctly in Excel.
    """
    subscribers = get_all_subscribers()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "mikrotik_username", "status", "monthly_fee", "expiry_date", "debt"])

    for sub in subscribers:
        status = sub["status"]
        debt = round(sub["monthly_fee"], 2) if status == "expired" else 0
        writer.writerow([
            sub["id"],
            sub["name"],
            sub["mikrotik_username"],
            status,
            sub["monthly_fee"],
            sub["expiry_date"],
            debt,
        ])

    csv_bytes = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=\"subscribers_export.csv\""},
    )


@app.get("/api/finance", dependencies=[Depends(require_auth)])
def get_finance():
    """Return debtors, unpaid totals, and total revenue from the billing DB."""
    from billing_system.database import get_connection, get_all_subscribers

    subscribers = get_all_subscribers()

    # Debtors: expired subscribers with an outstanding monthly fee.
    debtors = []
    for sub in subscribers:
        if sub["status"] == "expired":
            debtors.append(
                {
                    "name": sub["name"],
                    "mikrotik_username": sub["mikrotik_username"],
                    "amount": sub["monthly_fee"],
                    "expiry_date": sub["expiry_date"],
                }
            )

    total_unpaid = round(sum(d["amount"] for d in debtors), 2)

    conn = get_connection()
    try:
        row = conn.execute("SELECT SUM(amount) AS total FROM Payments").fetchone()
        total_revenue = round(row["total"] or 0, 2)
        row2 = conn.execute("SELECT COUNT(*) AS n FROM Payments").fetchone()
        payment_count = row2["n"]
    finally:
        conn.close()

    return {
        "total_unpaid": total_unpaid,
        "total_revenue": total_revenue,
        "payment_count": payment_count,
        "debtors": debtors,
        "expired_count": len(debtors),
        "subscriber_count": len(subscribers),
    }


@app.post("/api/sync", dependencies=[Depends(require_auth)])
def run_sync():
    """Manually trigger the full sync engine and return its summary."""
    manager = None
    try:
        manager = _get_connected_manager()
        summary = sync_mikrotik_status(mikrotik=manager)
        return summary
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.error("[API] Sync engine failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail={"error": "sync_failed", "message": "Sync engine failed."},
        ) from e
    finally:
        if manager is not None:
            try:
                manager.disconnect()
            except Exception:  # noqa: BLE001
                pass


# ── Global error handlers ────────────────────────────────────────────
@app.exception_handler(Exception)
def _unhandled_exception_handler(request, exc):  # noqa: ANN001
    """Keep the API from leaking stack traces — return clean JSON."""
    log.error("[API] Unhandled error on %s: %s", request.url, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": {"error": "internal_server_error", "message": "An unexpected error occurred."}},
    )


# ── No-cache middleware for the SPA (development) ────────
# Prevents the browser from caching old frontend files during
# development, so UI updates always reach the user.
# /api/* responses keep their normal JSON behavior.
@app.middleware("http")
async def disable_cache_for_frontend(request, call_next):
    """Add no-cache headers to non-API responses only."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ── Root UI routes (registered LAST so /api/* routes win) ──
# http://localhost:8000  -> the Phase 5 minimalist PWA
# SECURITY: We intentionally do NOT mount StaticFiles at "/" — that was
# vulnerable to Path Traversal (CVE-2024-24762) in older Starlette versions.
# Instead we serve explicit HTML pages with os.path.basename() validation,
# which blocks any ".." / "%2e" traversal payloads.
@app.get("/", include_in_schema=False)
def index():
    """Serve the SPA entry point (index.html)."""
    index_path = os.path.join(_FRONTEND_DIR, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "frontend not found"}, status_code=404)


@app.get("/{page}.html", include_in_schema=False)
def spa_page(page: str):
    """Serve a named HTML page safely (traversal-proof via basename)."""
    safe = os.path.basename(page)
    if safe != page:
        return JSONResponse({"error": "invalid path"}, status_code=400)
    target = os.path.join(_FRONTEND_DIR, f"{safe}.html")
    if os.path.isfile(target):
        return FileResponse(target)
    return JSONResponse({"error": "not found"}, status_code=404)
