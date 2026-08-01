# 🚀 Render Cloud Deployment — Al-Nathim (الناظم)

One-time setup. This app is **SQLite-based** and keeps its database on a [Render Persistent Disk](https://render.com/docs/disks). No external database service is required.

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

> 💡 `COOKIE_SECURE` auto-defaults to `true` in production (when `SQLITE_PATH` or `DATABASE_URL` is set). Leave `false` only for local HTTP development.

## 2. After deployment

- Open your Render URL → log in at `/login` with `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD`.
- Admins land automatically on the **Admin Center** at `/admin` (also linked in the bottom nav as "الإدارة"):
  - **طلبات التسجيل** — approve/reject pending self-registrations
  - **الأعضاء** — add/edit users, roles (مدير/وكيل), suspend/activate
  - **منح وصول** — grant timed access (days/hours/specific date)
  - **رموز الدخول** — generate invite/entry codes (activate accounts instantly)
- Without an invite code, new self-registrations go **pending** until you approve them in the Admin Center.
- **Data survives redeploys** because the DB lives on the persistent disk (`/var/data/mawlidati.db`).
- Before going public: change the admin password, consider `ALLOW_OPEN_REGISTRATION=false`, and set a strong `SECRET_KEY`.

### Data & backups

- **Back up** the disk: **More → نسخة احتياطية** downloads `mawlidati.db` directly.
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