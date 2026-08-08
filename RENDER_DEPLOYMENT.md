# 🚀 Render Cloud Deployment — Al-Nathim (الناظم)

One-time setup. The app is **SQLite-based**. On Render's **free plan** the recommended setup is the free **Turso cloud database** — your data survives every restart, redeploy, and monthly hour-reset with no paid disk. (A paid [Render Persistent Disk](https://render.com/docs/disks) is the alternative if you prefer self-managed storage.)

## 1. Create the Web Service

1. **New + → Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Build Command:** `./build.sh`
   - **Start Command:** `gunicorn app:app --workers 1 --threads 4 --worker-class gthread`
     > ⚠️ **Keep `--workers 1`.** SQLite does not support concurrent writes from multiple processes — one worker with several threads is the safe configuration.
4. **Add a Persistent Disk** (Render → your web service → Disks):
   - **Mount Path:** `/var/data`
   - **Size:** e.g. 1 GB (enough for many years of this data)
5. In **Environment** add these variables:

| Variable | Value |
|---|---|
| `SQLITE_PATH` | `/var/data/mawlidati.db` — **points the DB at your persistent disk** |
| `SECRET_KEY` | run `python -c "import secrets; print(secrets.token_hex(32))"` and paste the output |
| `SUPERADMIN_USERNAME` | `admin` (or your own) — **this is the system admin login** |
| `SUPERADMIN_PASSWORD` | **use a strong password** |
| `ALLOW_OPEN_REGISTRATION` | `true` (public register → pending approval) or `false` (only Admin Center can create accounts) |
| `SESSION_LIFETIME_MINUTES` | `480` (8 hours) — session duration |
| `MAX_FAILED_LOGINS` | `5` — auto-suspend account after this many wrong passwords (admin reactivates) |
| `LOGIN_RATE_LIMIT_ATTEMPTS` | `10` — brute-force budget per IP on /login + /register |
| `LOGIN_RATE_LIMIT_WINDOW_MINUTES` | `5` — the window the budget applies to |
| `COOKIE_SECURE` | `true` on Render (HTTPS) — session cookie only over HTTPS |

| `TURSO_DATABASE_URL` | `libsql://YOUR-DB-YOUR-ORG.turso.io` — **free cloud DB; set both Turso vars and skip the disk entirely** |
| `TURSO_AUTH_TOKEN` | your Turso DB token (create in the Turso dashboard → Database → Connect) |

> 💡 `COOKIE_SECURE` auto-defaults to `true` in production (when `SQLITE_PATH` or `DATABASE_URL` is set). Leave `false` only for local HTTP development.

## 2. After deployment

- Open your Render URL → log in at `/login` with `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD`.
- Admins land automatically on the **Admin Center** at `/admin` (also linked in the bottom nav as "الإدارة"):
  - **طلبات التسجيل** — approve/reject pending self-registrations
  - **الأعضاء** — add/edit users, roles (مدير/وكيل), suspend/activate
  - **منح وصول** — grant timed access (days/hours/specific date)
  - **رموز الدخول** — generate invite/entry codes (activate accounts instantly)
- Without an invite code, new self-registrations go **pending** until you approve them in the Admin Center.
- **Data survives redeploys** because it lives in your Turso database (or on the persistent disk if you used one).
- Before going public: change the admin password, consider `ALLOW_OPEN_REGISTRATION=false`, and set a strong `SECRET_KEY`.

### Data & backups

- **Back up** with the in-app **نسخ احتياطي** button (`/api/backup`): with Turso it downloads a full `.sql` dump of the whole cloud DB; locally it downloads `mawlidati.db`.
- Turso free plan includes **1-day point-in-time restore** — you can roll the DB back to any point in the last 24h from the Turso dashboard.
- Snapshot disk & resources are available in the Render dashboard (Disks → Snapshot).
- Your local Windows DB (`%APPDATA%\AlNathim\mawlidati.db`) is separate — production starts fresh.

## 3. Android app (optional)

Update the URL in `android_webview_app/app/src/main/java/com/alnathim/app/MainActivity.kt`:

```kotlin
private val APP_URL = "https://your-app.onrender.com"   // ← your real URL
```

## 4. Recommended (optional)

- Enable Render's **auto-deploy** on push to `main`
- Set a **health check** path (e.g. `/login`)
- Set **HTTP Strict Transport Security** once live (Render handles TLS termination)
- Keep `--workers 1` at all times (SQLite). Only a future Postgres migration would allow 2+ workers.
## 5. Per-account data isolation (⚠️ REQUIRES REDEPLOY)

The backend now supports **total per-account isolation**: every account
(admin or agent) sees **only its own data**. This was implemented in
`database.py` + `mobile_api.py`.

### What changed
- `database.py` has a per-request owner context (`set_current_owner()` /
  `current_owner()` / `_owner_filter()`).
- On startup, `init_db()` runs a migration: adds `owner_id` to
  `customers`, `invoices`, `payments`, `expenses`, `maintenance_tickets`,
  `packages`, `network_links`, `signal_cache` (idempotent, guarded).
- All existing rows are assigned to the **superadmin** account, so the
  superadmin keeps all current data.
- The mobile API auth decorators (`mobile_login_required` /
  `mobile_admin_required`) scope every request to the logged-in user and
  clear the scope afterwards.
- `generator_info` stays **shared** (single row, `CHECK(id = 1)`) — it is
  the company profile, not per-account data.

### ⚠️ Deploy steps (REQUIRED for isolation to work)
1. Push `database.py` + `mobile_api.py` to the Render repo.
2. Render will rebuild and restart; `init_db()` runs the migration once.
3. After deploy, **re-test**: login as a non-superadmin account and verify
   the list is empty; login as superadmin and verify all old data is still
   there.

### Behavioural note (updated after the FAT/payment module)
- **Read visibility is now SHARED** across the company (customers, packages,
  cabinets, invoices, expenses, tickets, network). Every account sees the
  same subscriber data.
- **Payment attribution**: agents see only their own collections; the admin
  (master) sees all payments with the collector name.
- **Editing/deleting is admin-only** (agents get 403 on PUT/DELETE).
- The web UI (`app.py`) does not set an owner yet, so it keeps showing all
  data (backward compatible).

## 6. FTTH FAT/Port + Payment Attribution module (new)

### What was added
- **Cabinets (FAT)**: new `cabinets` table + `fat_id`/`port_number` on
  `customers`. Ports are strictly validated (1..port_count, no duplicates
  per cabinet) — this kills the "Port 20 on a 16-port splitter" bug.
- **Payment attribution**: `payments` now carries `collected_by` +
  `collected_by_name`. Every mobile payment/quick-pay is stamped with the
  logged-in staff member automatically. Reports show a per-collector
  breakdown (`/report` → `collectors`).
- **Roles**: admin (master) sees everything and edits/deletes everything;
  agents (staff) see all shared customer/cabinet data, see **only their own
  payments**, and cannot edit or delete anything (403).
- **Import**: `POST /api/mobile/v1/import/customers` (admin) bulk-imports
  subscribers. Local helper: `tools/fat_import.py` converts legacy exports
  (CSV/TSV/TXT) into an `import_bundle.json`.

### API endpoints (mobile)
| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | /cabinets | all | list cabinets + occupancy |
| POST | /cabinets | admin | add cabinet |
| PUT/DELETE | /cabinets/{id} | admin | edit/delete cabinet |
| POST | /import/customers | admin | bulk import |

### ⚠️ Deploy steps
1. Push `database.py`, `mobile_api.py`, `app.py` to Render.
2. On restart, `init_db()` runs `_ensure_fat_columns()` automatically
   (adds columns; creates `cabinets`; no data loss).
3. `tools/fat_import.py` runs on the operator PC — NOT on Render.

### Notes
- `generator_info` remains shared (single row).
- The web UI (app.py) still works as before (no session owner → sees all;
  web payments have empty collector until web parity is wired).


## 7. Multi-account (companies) model — NEW architecture

The system is now **multi-tenant**: each ISP company registers its own
account and everything inside it is fully isolated from every other company.

### How it works
- **Self-registration**: from the login screen, a company creates its account
  (`POST /auth/register-company`). The first registrant becomes its admin.
- **Login flow**: pick the company → pick the user → password.
  (`GET /auth/accounts`, `GET /auth/accounts/{id}/users`).
- **Isolation**: `account_id` on every business table + a request-scoped
  account context. Company A can never see Company B's customers, packages,
  cabinets, payments, invoices or reports.
- **Inside a company**: customers/packages/cabinets are shared between its
  admin and workers; payments are attributed to the collector (workers see
  their own, the admin sees all); editing/deleting is admin-only.
- **Existing data** is preserved: the migration creates the default account
  and reassigns all current users/rows to it.

### Web (EXE/Desktop)
- The web login stores `account_id` in the session and `app.py` scopes every
  web request to it (new `before_request` hook), so the website is isolated
  per company too.

### ⚠️ Deploy steps
1. Push `database.py`, `mobile_api.py`, `app.py`, `auth.py` to Render.
2. On restart, `init_db()` runs `_ensure_account_columns()` automatically
   (creates `accounts`, adds `account_id`, assigns existing data — no loss).
3. Rebuild the APK (the login screen now has the company picker).
