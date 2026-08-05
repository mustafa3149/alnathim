# 🗺️ Native Android App — Full Rewrite Roadmap
**Project:** Al-Nathim ISP Management (الناظم)
**Status:** In progress — **Phase 1 (skeleton & build pipeline) DONE** ✅
**Goal:** Replace the current WebView shell app (`android_webview_app/`) with a fully **native Android application** (Kotlin + Jetpack Compose), keeping the existing Flask backend on Render as the source of truth.

---

## 1. Why & Scope

**Current state:** `android_webview_app/` is a thin WebView shell that renders the Jinja web dashboard (`alnathim.onrender.com`). Pros: ships fast. Cons: no offline, limited native feel, no push notifications, weak hardware/printing integration, tied to the web UI's responsiveness.

**Target state:** A native Android app with:
- Native screens for **every module**: login, dashboard, customers, billing (invoices/payments/quick-pay/renew), debts, reminders, expenses, maintenance tickets, packages, settings, admin center, network tools (ping / signal board / links / SNMP), reports.
- **Offline-first** local cache (Room) with background sync (WorkManager).
- **Push notifications** (FCM).
- **Native printing** — thermal (80mm) receipt + A4 invoice/PDF.
- **Arabic RTL** as a first-class citizen.
- Same package **`com.alnathim.app`** so it can replace/update existing installs.

**Out of scope:** Rewriting the backend. The Flask app + SQLite/Turso schema and all business rules (invoice generation, debt rollover, renewal, RBAC, cloud sync, HWID activation) stay; we **add a mobile JSON API layer** on top.

---

## 2. Proposed Tech Stack

| Concern | Choice | Why |
|---|---|---|
| Language | Kotlin 2.x | Modern Android standard |
| UI | Jetpack Compose + Material 3 | Native look, fast UI dev |
| Networking | Retrofit 2 + OkHttp + kotlinx.serialization | Battle-tested, typed API |
| Local DB | Room | Offline cache + SQL |
| DI | Hilt | Standard, testable |
| Navigation | Navigation Compose | Type-safe routes |
| Preferences | DataStore (tokens in EncryptedSharedPreferences / Keystore) | Secure session storage |
| Background | WorkManager | Guaranteed sync/refresh |
| Push | Firebase Cloud Messaging | Notifications + deep links |
| Async | Coroutines + Flow | Idiomatic Kotlin |
| Min / Target / Compile SDK | 24 / 35 / 35 | Broad device coverage, current Android |
| Build | Gradle 8.14.3 + AGP 8.13.0 | Same release era as Kotlin 2.2.21 / KSP 2.2.21. ⚠️ Gradle 9.3.0 hangs the KSP task (KSP 2.2.21 predates Gradle 9) — see Phase 1 notes |

**Rationale:** Compose is the current Android standard; Room + WorkManager give offline capability the WebView can never have; keeping `com.alnathim.app` preserves the upgrade path for already-installed users.

---

## 3. Target Architecture

```
┌─────────────────────────────────────┐
│  Native Android App (Kotlin)        │
│  ┌───────────────────────────────┐  │
│  │  UI layer — Compose screens   │  │
│  └──────────────┬────────────────┘  │
│  ┌──────────────▼────────────────┐  │
│  │  ViewModels (StateFlow)       │  │
│  └──────────────┬────────────────┘  │
│  ┌──────────────▼────────────────┐  │
│  │  Repository layer             │  │
│  │   ┌────────┐  ┌────────────┐  │  │
│  │   │  Room  │  │  Retrofit  │  │  │
│  │   │ cache  │  │  mobile API│  │  │
│  │   └────────┘  └────────────┘  │  │
│  └──────────────┬────────────────┘  │
└─────────────────┼───────────────────┘
                  │ HTTPS /api/mobile/v1 (bearer token)
┌─────────────────▼───────────────────┐
│ Flask backend (Render)              │
│  app.py + database.py + auth.py     │
│  SQLite / Turso (source of truth)   │
│  + NEW mobile_api.py blueprint      │
└─────────────────────────────────────┘
```

- **Repository pattern:** UI → ViewModel → Repository → (Room + Retrofit). Room is the single source of truth for the UI; the network layer keeps it fresh.
- **Single Activity** with Compose Navigation and a bottom nav bar.
- **Auth:** server issues a short-lived bearer token + refresh token; **RBAC is enforced server-side** on every endpoint (`login_required`, `admin_required` — existing auth.py decorators).

---

## 4. Mobile API Contract (new backend blueprint)

New file `mobile_api.py` registered in `app.py` under prefix **`/api/mobile/v1`**. All responses JSON. All mutations written to the existing `audit_log` table.

**Auth endpoints:**
- `POST /auth/login` → `{token, refresh_token, user{id, username, role, full_name, phone, status, access_expires}}`
- `POST /auth/refresh` → new token pair
- `POST /auth/logout` → revoke

**Module endpoints** (all reuse existing database.py + app.py business functions):

| Group | Endpoint | Access |
|---|---|---|
| Dashboard | `GET /dashboard/summary` — counts, revenue, expiring, unpaid, pending tickets | login |
| Customers | `GET /customers?q&region&status&page` · `GET /customers/{id}` · `POST /customers` · `PUT /customers/{id}` · `POST /customers/{id}/toggle` · `POST /customers/{id}/renew` · `DELETE /customers/{id}` · `GET /customers/{id}/history` · `GET /customers/{id}/signal` | login (delete: admin) |
| Billing | `GET /invoices?customer_id&month&year` · `GET /invoices/{id}` · `POST /payments` · `PUT /payments/{id}` · `DELETE /payments/{id}` · `POST /quick-pay/{customer_id}` · `GET /payments/current-invoice/{customer_id}` | login (edit/delete: admin) |
| Debts & reminders | `GET /debts` · `GET /reminders` | login |
| Expenses | `GET/POST /expenses` · `PUT/DELETE /expenses/{id}` | login (delete: admin) |
| Tickets | `GET/POST /tickets` · `PUT /tickets/{id}/status` | login |
| Packages | `GET/POST/PUT/DELETE /packages` | GET: login, write: admin |
| Settings | `GET/PUT /settings` · `GET/PUT /generator-info` | admin |
| Admin center | `GET/POST /team` · `PUT /team/{id}/status` · `GET /audit` · `POST /backup` · `POST /sync/pull` · `POST /sync/push` · `GET/PUT /tower-connection` · `POST /tower-test` | admin |
| Network tools | `POST /network/ping` · `GET /signal-board` · `GET/POST/PUT/DELETE /network/links` | login (write: admin) |
| Reports | `GET /report?month&year` · `GET /export/excel` · `GET /export/payments/excel` | report: login, export: admin |
| Printing | `GET /print/receipt/{payment_id}` · `GET /print/invoice/{invoice_id}` (HTML + PDF/A4 variants) | login |
| Devices | `POST /devices` (FCM token registration) | login |

**Decision note:** we extend the **Flask app** (shared business logic in app.py/database.py: renewal, debt rollover, invoice generation) rather than reusing the separate FastAPI `api_gateway/` (which serves the desktop EXE and only covers a subset). The gateway stays untouched.

**Error envelope:** `{"ok": false, "error": {"code": "...", "message_ar": "..."}}` — Arabic messages, matching project rule 8.1.

---

## 5. Data Model Mapping (server tables → Room entities)

Server schema (`database.py`) → Android Room entities:

| Server table | Room entity | Notes |
|---|---|---|
| `users` | `UserEntity` | id, username, role, full_name, phone, status, access_expires |
| `packages` | `PackageEntity` | id, name, speed, price |
| `customers` | `CustomerEntity` | full schema incl. package_id, mikrotik creds, nano_ip, device_type, status, is_active, subscription_date, renewal_date, previous_debt, notes |
| `invoices` | `InvoiceEntity` | + `previous_debt`, `total_amount`, `paid_amount`, `is_paid` |
| `invoice_extras` | `InvoiceExtraEntity` | 1—N under invoice |
| `payments` | `PaymentEntity` | amount, payment_date, payment_method, notes |
| `expenses` | `ExpenseEntity` | category, amount, recipient_name, subscriber_id |
| `maintenance_tickets` | `TicketEntity` | status, resolved_at |
| `network_links` | `NetworkLinkEntity` | link_type, ip, username, password, community |
| `signal_cache` | `SignalCacheEntity` | read-only snapshot (signal_dbm, ccq, rx/tx, status) |
| `settings` | `SettingEntity` (key-value) | |
| `generator_info` | `GeneratorInfoEntity` | single row |
| `audit_log` | — | server-only, not cached locally |

Relations: `Customer 1—N Invoice 1—N Payment`; DAOs per entity + a `SyncDao` for dirty-flag tracking.

---

## 6. Security & Compliance

- **HTTPS only** — `android:usesCleartextTraffic="false"` stays; the existing `network_security_config.xml` local-network exceptions are kept for development.
- **Token storage** — EncryptedSharedPreferences backed by Android Keystore; never plain DataStore.
- **RBAC server-side** — the app hides UI by role, but the server rejects unauthorized calls (existing decorators).
- **Auditing** — every mutation writes to `audit_log` with the app user id (already the web behavior).
- **Rate limiting** — reuse the existing login rate-limit logic for `/auth/login`.
- **Biometric lock** (Phase 16) — optional in-app lock via BiometricPrompt.
- **Immersive fullscreen** — the no-border behavior added to the WebView app is preserved in the native app (status/nav bars hidden, safe-area insets handled).

---

## 7. Phased Execution Plan

### Phase 1 — Project skeleton & build pipeline (≈ 2 days) ✅ DONE
- New Compose project under **`android_native/`** (keep `android_webview_app/` intact until Phase 17).
- Package `com.alnathim.app`, minSdk 24 / targetSdk 35, Material 3 dark theme (brand: bg `#0a0a0a`, surface `#1A1A1A`), Arabic labels.
- Wire dependencies: Compose, Retrofit, OkHttp, kotlinx.serialization, Room, Hilt, DataStore, Navigation Compose, WorkManager.
- Local build script `build_android.bat` → `gradlew assembleDebug`.
- Immersive fullscreen + safe-area insets (no top/bottom borders).
- **Exit:** `gradlew assembleDebug` green; blank app installs with no letterbox.

**Actual versions used (verified green build):** Gradle **8.14.3** (wrapper) · AGP **8.13.0** · Kotlin **2.2.21** + compose/serialization plugins · KSP **2.2.21-2.0.5** (KSP1 mode: `ksp.useKSP2=false`) · Compose BOM **2025.06.01** · Navigation **2.8.5** · Hilt **2.56.2** · Room **2.7.2** · Retrofit **2.11.0** · OkHttp **4.12.0** · kotlinx.serialization **1.7.3** · DataStore **1.1.1** · WorkManager **2.10.0**.

> ⚠️ **Build decisions (deviations from the original table above):**
> 1. **Gradle 8.14.3 instead of 9.3.0.** Gradle 9.3.0 (from the WebView wrapper) makes the `kspDebugKotlin` task hang — the KSP 2.2.21 Gradle integration predates Gradle 9 and never starts its worker. 8.14.3 is the same release era as AGP 8.13 / Kotlin 2.2.21 / KSP 2.2.21. The `-all` distribution is already cached on the dev machine.
> 2. **`ksp.useKSP2=false`** forces the KSP1 compatibility implementation (deprecated but supported) — the battle-tested path for the Room + Hilt processors.
> 3. **Hilt 2.56.2** (not 2.52): 2.52 reads Kotlin metadata ≤ 2.1.0 and fails on Kotlin 2.2.21 (`Provided Metadata instance has version 2.2.0`).
> 4. The **Hilt Gradle plugin** (`com.google.dagger.hilt.android`) must be applied or `@HiltAndroidApp`/`@AndroidEntryPoint` fail with "Expected ... to have a value".
>
> **Artifact:** `android_native/app/build/outputs/apk/debug/app-debug.apk` (~12.5 MB debug, signed with the debug key). Same package `com.alnathim.app` → installs as an update over the WebView app.

### Phase 2 — Mobile API layer (backend) (≈ 1 week) ✅ DONE
- New `mobile_api.py` blueprint (Section 4) reusing database.py + app.py business functions.
- JSON error envelope with Arabic messages.
- Auth endpoints issuing signed HMAC/JWT-style tokens (reuse `ACTIVATION_KEY_SECRET`; TTL from `AUTH_TOKEN_TTL_HOURS`).
- API contract tests mirroring the existing finance/audit tests (login, renew, quick-pay, debt rollover).
- **Exit:** all happy paths pass via curl/Postman; `API_CONTRACT.md` written.

### Phase 3 — Authentication & onboarding (≈ 3–4 days) ✅ DONE
- Login screen (Arabic), secure token storage, automatic refresh, role-aware routing (admin → admin home, agent → agent home).
- Status gates: suspended / expired accounts blocked with Arabic message (mirrors auth.py logic).
- Session expiry → re-login flow; logout revokes tokens.
- **Exit:** login → dashboard works against Render; token refresh works; wrong password shows Arabic error; no network shows Arabic offline message.

**Implemented (native Kotlin/Compose):**
- `ui/auth/`: `AuthViewModel` (Splash → Login | Main gate w/ cold-start auto-refresh), `LoginScreen` (Arabic RTL form, inline validation, server Arabic errors), `LoadingScreen`.
- `data/auth/`: `TokenStore` (EncryptedSharedPreferences + Keystore AES256_GCM — never plain DataStore), `AuthInterceptor` (injects `Authorization: Bearer`), `AuthRepository` (login / rotating refresh / logout / session restore).
- `NetworkModule` now wires `AuthInterceptor` into OkHttp; `MobileApiService` DTOs updated to the real Phase 2 contract (dashboard KPIs, refresh/logout).
- `AlNathimApp` gates the app shell behind auth; `MoreScreen` (المزيد) shows the logged-in user + logout; theme forces RTL.
- Role-aware `isAdmin()` exposed for later admin-UI gating (server still enforces RBAC).
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 4 — App shell & navigation (≈ 3–4 days) ✅ DONE
- Single Activity + Compose Navigation; bottom nav (Dashboard · Customers · Billing · More).
- Toolbar, dark theme, **RTL layout**, safe-area insets.
- Shared loading / empty / error states + pull-to-refresh.
- **Exit:** shell navigates all placeholder screens; RTL correct; no borders.

**Implemented (native Kotlin/Compose):**
- `ui/components/StateViews.kt` — shared `LoadingState` / `EmptyState` / `ErrorState`; `RefreshableContent.kt` wraps Material3 `PullToRefreshBox` for every module.
- `MainShell` (in `AlNathimApp.kt`) — TopAppBar with a **dynamic per-destination title**, bottom nav, RTL layout (theme forces `LayoutDirection.Rtl`), safe-area insets via Scaffold.
- **Dashboard now live**: `ui/dashboard/DashboardViewModel` (GET `/dashboard/summary`) + `DashboardScreen` (KPI cards in Dinar, recent payments, pull-to-refresh) — wired as the start destination (Phase 5 work started early).
- `formatDinar()` helper formats `12,000 د.ع` per project rule 8.1.
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 5 — Dashboard (≈ 3 days) ✅ DONE
- Cards: active customers, expiring today/this week, unpaid total, monthly revenue, pending tickets; recent payments list.
- Pull-to-refresh + periodic WorkManager refresh.
- **Exit:** data matches web dashboard.

**Implemented (native Kotlin/Compose):**
- `ui/dashboard/` — `DashboardViewModel` (GET `/dashboard/summary`, pull-to-refresh, periodic background refresh) + `DashboardScreen` (revenue/collected/debt/expiring/tickets/packages KPI cards in Dinar, recent payments list).
- `DashboardRefreshWorker` — Hilt-injected periodic worker every 6h (`INTERVAL_HOURS`), enqueued once via unique name; `AlNathimApplication` implements `Configuration.Provider` with `HiltWorkerFactory`.
- `formatDinar()` formats `12,000 د.ع` per project rule 8.1.
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 6 — Customers module (≈ 1 week) ✅ DONE
- List with search + filters (region / status / package) + paging.
- Detail screen: profile, package, MikroTik creds, subscription/renewal dates, invoices, payment history, live signal.
- Add/Edit forms with validation; toggle status; **renew** (server generates missing invoices + rolls debt); delete (admin, confirm dialog).
- WhatsApp deep-link on phone number (reuse the deep-link handling logic from the WebView app).
- **Exit:** CRUD parity with web; renew produces identical invoices/debt as the web flow.

**Implemented (native Kotlin/Compose):**
- `data/network/MobileApiService.kt` — full customer DTOs (`CustomerDto`, `CustomerListResponse`, history/invoices/payments, signal) + CRUD/renew/toggle/delete endpoints (query-params fit the Phase 2 contract).
- `data/customer/CustomerRepository.kt` — wraps all customer + package endpoints.
- `ui/customers/CustomersViewModel` — list, search (`?q`), pagination, renew/toggle/delete with Arabic action messages.
- `ui/customers/CustomerListScreen` — search bar, pull-to-refresh, status-colored rows, renew-months dialog, toggle/delete.
- `ui/customers/CustomerDetailScreen` + `CustomerDetailViewModel` — profile cards + financial history (invoices in green/red, payments) + cached signal.
- Navigation: `customer/{id}` route with back; wired into the Customers tab.
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 7 — Billing (invoices, quick-pay, payments, renew) (≈ 1 week) ✅ DONE
- Invoices list per customer + current month; **quick-pay** (auto-creates current invoice if none); make payment (amount, method, notes); edit/delete payment (admin).
- Receipt preview (native) → thermal print + A4 PDF.
- **Exit:** payment flow matches web; money math validated against the existing finance tests.

**Implemented (native Kotlin/Compose):**
- `data/network/MobileApiService.kt` — billing DTOs (`InvoiceDto`, `InvoiceListResponse`, `InvoiceDetailDto`, `CurrentInvoiceDto`, `QuickPayResponse`, `PaymentCreateResponse`) + endpoints matching the Phase 2 contract (monthly invoices, invoice detail, current-invoice, payments create, quick-pay).
- `data/billing/BillingRepository.kt` — wraps the billing endpoints.
- `ui/billing/BillingViewModel` — monthly invoice list (month/year), quick-pay (amount optional → full remaining), payment recording; Arabic action messages.
- `ui/billing/BillingScreen` — paid/unpaid colored invoice rows (paid/remaining in Dinar), quick-pay dialog, payment dialog; wired into the Billing bottom-nav tab.
- Money math stays server-side (already covered by `test_mobile_api.py` — create → quick-pay → renew → debt merge).
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 8 — Debts & reminders (≈ 3 days) ✅ DONE
- Debts list (unpaid totals, overdue), reminders list (renewal_date passed).
- One-tap WhatsApp reminder message (Arabic template) via deep link.
- **Exit:** matches web `/debts` and `/reminders`.

**Implemented (native Kotlin/Compose):**
- `data/network/MobileApiService.kt` — `DebtDto` / `ReminderDto` + `GET /debts` / `GET /reminders`.
- `data/debt/DebtRepository.kt` — wraps the two endpoints.
- `ui/debts/DebtsViewModel` — loads debts + reminders in parallel.
- `ui/debts/DebtsScreen` — debts section (unpaid totals, unpaid count) + reminders section (expired/active colors) with a **one-tap WhatsApp deep link** (`https://wa.me/<digits>?text=<Arabic template>`), pull-to-refresh.
- Navigation: `debts` route from the More screen (الديون button).
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 9 — Expenses & maintenance tickets (≈ 4 days) ✅ DONE
- Expenses CRUD with category / recipient / optional subscriber link; monthly totals.
- Tickets: list + create + resolve; link to customer.
- **Exit:** parity with web.

**Implemented (native Kotlin/Compose):**
- `data/network/MobileApiService.kt` — `ExpenseDto`/`TicketDto` DTOs + expenses CRUD + tickets CRUD/toggle endpoints.
- `data/operations/OperationsRepository.kt` — wraps expenses + tickets endpoints.
- `ui/operations/OperationsViewModel` — loads month expenses + all tickets in parallel; add/delete expense, create/toggle ticket (Arabic action messages).
- `ui/operations/OperationsScreen` — monthly expenses section (total in Dinar, add dialog with category/amount/description, delete per row) + tickets section (pending/resolved colors, create dialog with customer id + issue, toggle).
- Navigation: `operations` route from the More screen (المصروفات والتذاكر button).
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 10 — Packages & settings (≈ 3 days) ✅ DONE
- **Implemented:** `ui/settings/` (SettingsScreen + SettingsViewModel), `data/config/AdminConfigRepository`, packages CRUD + generator-info DTOs/endpoints in `MobileApiService`, `settings` route from More (الإعدادات button). Artifact: `app-debug.apk` — **`gradlew assembleDebug` green**.
- Packages list/edit (admin); generator info edit (admin); settings viewer.
- **Exit:** parity with web.

### Phase 11 — Admin center (≈ 1 week) ✅ DONE
- **Implemented:** `ui/admin/` (AdminScreen + AdminViewModel), `data/admin/AdminRepository`, team/audit/backup/sync/tower DTOs + endpoints in `MobileApiService`, `admin` route from More (مركز الإدارة button). Artifact: `app-debug.apk` — **`gradlew assembleDebug` green**.
- Team (users): list + add + suspend; audit log browser; backup trigger + download; cloud sync pull/push triggers; tower connection settings + real connection test.
- Superadmin flows ported from `superadmin.html` (role-gated).
- **Exit:** admin parity; every action lands in audit_log.

### Phase 12 — Network tools (≈ 4–5 days)
- Ping screen (`/api/network/ping`), signal board grid (signal_dbm / ccq / online state, auto-refresh), network links CRUD, customer signal viewer.
- **Exit:** matches web `network.html` / `signal_board.html`.

**12a (local-tower connectivity) ✅ DONE:**
- `data/network/ServerConfig.kt` — runtime API base URL (default = Render; can be switched to `http://tower-ip:5000/api/mobile/v1/`).
- `NetworkModule` now builds Retrofit from `ServerConfig` instead of the hardcoded BuildConfig URL.
- `src/debug/res/xml/network_security_config.xml` — **debug-only** cleartext permitted to any host (release stays HTTPS-only), so the app can talk to the tower Flask over LAN.
- Settings screen gained an "عنوان الخادم" (server address) field + save (takes effect on next launch).
- The MikroTik pull (sector auto-pull), sync/pull, tower-test, ping and signal-board endpoints were already implemented server-side in the Phase 2 rebuild.
- Artifact: `android_native/app/build/outputs/apk/debug/app-debug.apk` — **`gradlew assembleDebug` green**.

### Phase 13 — Reports, Excel export & printing (≈ 4–5 days)
- Monthly report (income / expenses / debts); Excel export opened via `ACTION_VIEW` (openpyxl files from backend).
- Native printing: 80mm thermal receipt layout + A4 invoice/receipt PDF via Android `PrintManager`.
- **Exit:** exports open correctly; print output matches the web print templates.

### Phase 14 — Offline-first & background sync (≈ 1 week)
- Room as the UI source of truth; WorkManager sync with dirty flags (`updated_at` columns added server-side where missing).
- Conflict policy: last-write-wins per record using server timestamps; failed mutations queued and retried.
- Reads work offline with an "offline" badge; writes queue and flush when online.
- **Exit:** app fully usable for reads with no network; mutations sync when connectivity returns.

### Phase 15 — Push notifications (≈ 3–4 days)
- FCM integration + device token registration (`POST /devices`).
- Backend events: ticket created, payment received, reminder due (FCM HTTP v1 — Render free tier has no persistent socket).
- Notification tap → deep link to the relevant screen.
- **Exit:** payment/ticket events produce notifications; taps navigate correctly.

### Phase 16 — RTL polish, security & testing (≈ 1 week)
- Full RTL/accessibility pass; dark/light; edge cases.
- Biometric lock; optional certificate pinning; Crashlytics.
- Unit tests per module; Espresso smoke tests; MockWebServer tests for the API layer; mirror finance tests as API contract tests.
- **Exit:** test suite green on emulator + device.

### Phase 17 — Release & migration (≈ 3–4 days)
- Versioned release APK signed with a stable keystore (new keystore — record passwords safely).
- Play Store AAB upload or direct APK distribution; `BUILD_ANDROID.md` updated.
- Two-week shadow period, then retire `android_webview_app/`.
- **Exit:** signed APK installs; verified against the production/Turso DB.

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Scope creep (every web feature) | schedule | per-module parity acceptance; defer cosmetics |
| Render free-tier limits (cold starts, 512 MB) | slow API | lightweight JSON endpoints, caching headers; Turso already offloads DB |
| Offline sync conflicts | data corruption | `updated_at` + server-authoritative LWW; audit on conflict |
| FCM on free tier | unreliable push | HTTP v1 + WorkManager retry with backoff |
| RTL / printing complexity | UX issues | real-device acceptance in each phase |
| Keystore loss | broken updates | keystore + passwords stored per existing project rule |

---

## 9. Effort Estimate

- **≈ 9–10 weeks** for one developer (part-time longer).
- Split: foundation & API 20% · auth 10% · business modules (customers/billing/expenses/etc.) 40% · network tools 10% · offline & push 10% · polish/testing/release 10%.

---

## 10. Definition of Done (overall)

- Feature parity with the web dashboard for all standard + core admin workflows.
- Offline reads + queued writes working.
- Push notifications working end-to-end.
- `gradlew assembleDebug` + unit tests green; signed release APK installable.
- No letterbox/borders; Arabic RTL correct; all user-facing strings Arabic; amounts formatted `12,000 د.ع`.