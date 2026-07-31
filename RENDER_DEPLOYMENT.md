# 🚀 Render Cloud Deployment — Al-Nathim (الناظم)

One-time setup. The `build.sh` already handles dependencies; below is what you must configure in the Render dashboard.

## 1. Database (this project uses Supabase PostgreSQL)

The local `.env` already contains the Supabase connection string. **For production you must set the same credentials as Render environment variables** — do NOT rely on `.env` (it is git-ignored and Render never reads it).

If you ever need a new one: Supabase dashboard → Project Settings → Database → **Connection string (URI)** → copy the pooler URL on port `6543`.

## 2. Create the Web Service
1. **New + → Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Build Command:** `./build.sh`
   - **Start Command:** `gunicorn app:app`
4. In **Environment** add these variables:

| Variable | Value |
|---|---|
| `DATABASE_URL` | (the Supabase pooler connection string, e.g. `postgresql://postgres.<ref>:<password>@aws-0-ap-southeast-2.pooler.supabase.com:6543/postgres`) |
| `SECRET_KEY` | run `python -c "import secrets; print(secrets.token_hex(32))"` and paste the output |
| `SUPERADMIN_USERNAME` | `admin` (or your own) |
| `SUPERADMIN_PASSWORD` | **change this** — use a strong password |

> ⚠️ **Hard requirement:** as soon as `DATABASE_URL` points to PostgreSQL, the app **refuses to boot** if `SECRET_KEY` is the known default or `SUPERADMIN_PASSWORD` is `admin123`. This is intentional (see `config.py` safety guard).

5. **Create Web Service** → wait for the first deploy to finish
6. You get a URL like `https://your-app.onrender.com`

## 3. After deployment
- Open your Render URL, log in with the demo owner (`demo` / `demo123`) **or** register a new owner
- Log into the SuperAdmin panel at `/superadmin/login` with `SUPERADMIN_USERNAME` / `SUPERADMIN_PASSWORD`
- Update the Android app URL in `android_webview_app/app/src/main/java/com/alnathim/app/MainActivity.kt`:
  ```kotlin
  private val APP_URL = "https://your-app.onrender.com"   // ← your real URL
  ```

## 4. Recommended (optional)
- **Multiple workers:** change start command to `gunicorn app:app --workers 2 --threads 4` (Postgres only; keep `--workers 1` if using SQLite)
- Enable Render's **auto-deploy** on push to `main`
- Set **HTTP Strict Transport Security** / health checks once live

---

### Versioning with SQLite + PostgreSQL
The app auto-detects: no `DATABASE_URL` → local SQLite (`%APPDATA%\AlNathim\mawlidati.db`); `DATABASE_URL` set → PostgreSQL. Your existing local data **does not** migrate automatically — production starts with a fresh database (new owners register their own accounts).