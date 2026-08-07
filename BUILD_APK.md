# 📱 Build the Al-Nathim APK

This is a standard Android **WebView** app that wraps the Al-Nathim web dashboard. The app URL is defined once in `MainActivity.kt`:

```kotlin
private val APP_URL = "https://alnathim.onrender.com/"
```

**Before building:** make sure `APP_URL` points to your real deployed Render URL (it already does by default).
If you change it later, edit `android_webview_app/app/src/main/java/com/alnathim/app/MainActivity.kt` and rebuild.

---

## Option A — Android Studio (recommended, easiest)

1. **Install Android Studio** → https://developer.android.com/studio
2. **Open the project:** File → Open → select the `android_webview_app` folder.
3. Let Gradle sync finish (first time downloads dependencies — needs internet).
4. **Build the APK:**
   - Menu: **Build → Generate Signed App Bundle / APK…**
   - Choose **APK** → **Next**
   - **Create new keystore** (or use an existing one) — this is your app signature:
     - Key store path: pick a safe folder, e.g. `C:\keys\alnathim.jks`
     - Password + Alias + password (write these down — you need them for every update)
   - Choose **release** build type → **Create**
5. **Result:** the signed APK is at
   `android_webview_app\app\release\app-release.apk`
   — share this file directly; anyone can install it.

---

## Option B — Command line (no Android Studio UI)

Prerequisites: **JDK 17** and the **Android SDK** (or just install Android Studio once; its bundled SDK works).

1. From the project root, build a debug APK to verify the project compiles:
   ```
   cd android_webview_app
   gradlew.bat assembleDebug
   ```
   Debug APK: `app\build\outputs\apk\debug\app-debug.apk`

2. **Create a signed keystore** (one time):
   ```
   keytool -genkeypair -v -keystore C:\keys\alnathim.jks ^
     -keyalg RSA -keysize 2048 -validity 10000 ^
     -alias alnathim -storepass YOUR_STORE_PASS -keypass YOUR_KEY_PASS
   ```

3. **Sign a release APK:**
   - Build the unsigned release:
     ```
     gradlew.bat assembleRelease
     ```
     Output: `app\build\outputs\apk\release\app-release-unsigned.apk`
   - Sign it with `apksigner` (from the Android SDK build-tools):
     ```
     apksigner sign --ks C:\keys\alnathim.jks ^
       --ks-key-alias alnathim ^
       --ks-pass pass:YOUR_STORE_PASS ^
       --out alnathim-signed.apk ^
       app\build\outputs\apk\release\app-release-unsigned.apk
     ```
   - Verify:
     ```
     apksigner verify alnathim-signed.apk
     ```
   - **Result:** `alnathim-signed.apk` — share it.

> 💡 The `gradlew` (Linux/macOS) / `gradlew.bat` (Windows) wrapper is already included, so no system Gradle is required.
> **Keep your keystore + passwords safe.** Without them you cannot update the app later (Android requires the same signature for updates).

---

## What the app already handles (no code changes needed)

- **Login session, dashboard, customers, invoices, payments, debts, report, network tools** — all rendered inside the WebView from the **bundled** `mobile_app/` UI (Blood-style: local-first, server APIs refresh data).
- **Session restore** — the login page verifies the saved token via `GET /api/mobile/v1/auth/me` (added to the backend so the app no longer bounces back to login).
- **WhatsApp deep links** (wa.me / api.whatsapp.com) → open the real WhatsApp app.
- **`window.open()` popups** (print receipts, print invoices) → shown inside the WebView.
- **`alert()` / `confirm()` JS dialogs** (delete confirmations) → native Android dialogs.
- **Back button** → navigates WebView history; on first page → moves app to background.
- **Fullscreen, no borders** — the system status/navigation bars are hidden (immersive mode), so the app fills the whole screen with **no black bar at the top or bottom**. Safe-area insets keep content clear of notches, gesture bars, and the on-screen keyboard.
- **RTL Arabic layout**, dark/light theme sync, and **Android 11+ package visibility** for WhatsApp.
- **Cleartext traffic disabled** — HTTPS only (matches your Render deployment).

## Syncing a UI change into the APK

The bundled web UI lives in two places that must stay in sync:

```
mobile_app/            ← source of truth (edit here)
android_webview_app/app/src/main/assets/mobile/  ← what goes into the APK
```

After editing `mobile_app/`, re-sync and rebuild:

```bat
xcopy /E /I /Y mobile_app android_webview_app\app\src\main\assets\mobile
cd android_webview_app
gradlew.bat assembleDebug
```

## Backend (needed for the app to work)

The app talks to `https://alnathim.onrender.com/api/mobile/v1` (Flask). If you
change `mobile_api.py`, run the contract tests and deploy (push to `main` →
Render auto-deploys):

```bat
set PYTHONIOENCODING=utf-8 && py test_mobile_api.py   (63 assertions)
```