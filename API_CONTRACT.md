# 📱 Al-Nathim Mobile API Contract — `/api/mobile/v1`

**Project:** Al-Nathim ISP Management (الناظم) — Native Android app backend
**Status:** Phase 2 ✅ — implemented, registered, and covered by `test_mobile_api.py`
**Base URL:** `https://alnathim.onrender.com/api/mobile/v1` (dev: `http://localhost:5000/api/mobile/v1`)

---

## 1. Response Envelope

Every endpoint returns JSON. Mutations are written to `audit_log` with the
token user's id (project rule 8.1 — Arabic user-facing messages).

```json
// Success
{ "ok": true, "data": { ... } }

// Error
{ "ok": false, "error": { "code": "not_found", "message_ar": "المشترك غير موجود" } }
```

### Error codes

| HTTP | code | Meaning |
|---|---|---|
| 400 | `invalid_input`, `duplicate`, `invalid_credentials`, `already_paid`, `invalid_amount`, `connection_failed` | Bad request / business validation |
| 401 | `unauthorized` | Missing / invalid / expired / revoked token |
| 403 | `forbidden` | Valid token but not admin (RBAC) |
| 403 | `suspended`, `pending`, `locked`, `expired` | Account status gate at login |
| 404 | `not_found` | Resource missing |
| 429 | `rate_limited` | Per-IP login brute-force throttle |
| 500 | `server_error` | Unhandled server failure |
| 501 | `unsupported` | Backup unsupported on Turso |

---

## 2. Authentication

- Access token TTL: `AUTH_TOKEN_TTL_HOURS` (default 24h).
- Refresh token TTL: `AUTH_REFRESH_TTL_DAYS` (default 30d).
- Tokens are HMAC-SHA256 signed with `ACTIVATION_KEY_SECRET`, format:
  `typ.user_id.exp.jti.sig`.
- Send: `Authorization: Bearer <access-token>`.
- Refresh tokens rotate on refresh and are revoked on logout/rotation
  (`mobile_revoked_tokens` table).
- RBAC is enforced **server-side** on every endpoint.

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | `/auth/login` | — | `{username, password}` → `{token, refresh_token, expires_in, user}` |
| POST | `/auth/refresh` | — | `{refresh_token}` → rotated token pair |
| POST | `/auth/logout` | access | Revokes access + refresh (if passed in body) |

---

## 3. Endpoints

All require `Bearer` access token; `admin` marks admin-only.

### Dashboard
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/dashboard/summary` | login | KPIs: active customers, expected/collected, total debt, expiring, pending tickets, recent payments |

### Customers
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/customers?q&region&status&debt&page&per_page` | login | Paginated list (per_page ≤ 200) |
| GET | `/customers/{id}` | login | Full profile |
| POST | `/customers` | login | Create; auto-creates current-month invoice (package × months + previous debt; `paid_in_full` marks debt settled) |
| PUT | `/customers/{id}` | login | Edit (keeps invoice legacy package fields consistent) |
| POST | `/customers/{id}/toggle` | login | Flip `is_active` |
| POST | `/customers/{id}/renew` | login | Renew N months; rolls unpaid debt into invoice. If a current-month invoice exists, **merges** carried debt into it (UNIQUE constraint) |
| DELETE | `/customers/{id}` | admin | Hard delete (cascades) |
| GET | `/customers/{id}/history` | login | Invoices + payments (+ extras) |
| GET | `/customers/{id}/signal` | login | Cached SNMP signal for customer IP |

### Billing
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/invoices?customer_id&month&year&search` | login | Invoice list |
| GET | `/invoices/{id}` | login | Invoice + extras + payments |
| GET | `/payments/current-invoice/{customer_id}` | login | Current month's invoice (or `null`) |
| POST | `/payments` | login | Record payment on invoice (clamps to remaining) |
| PUT | `/payments/{id}` | admin | Edit payment (recalculates invoice paid/is_paid) |
| DELETE | `/payments/{id}` | admin | Delete payment (recalculates invoice) |
| POST | `/quick-pay/{customer_id}` | login | Auto-creates current invoice if none; records payment; syncs router debt |

### Debts & reminders
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/debts` | login | Per-customer unpaid totals |
| GET | `/reminders` | login | Customers with debt or past renewal |

### Expenses
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/expenses?month&year&search&category` | login | Month expenses + total |
| POST | `/expenses` | login | Add expense (`subscriber_id` optional) |
| PUT | `/expenses/{id}` | login | Edit expense |
| DELETE | `/expenses/{id}` | admin | Delete expense |

### Maintenance tickets
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/tickets?status&search` | login | List |
| POST | `/tickets` | login | Create (`customer_id` + `issue_description`) |
| PUT | `/tickets/{id}/status` | login | Toggle pending/resolved |

### Packages
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/packages` | login | List all |
| POST | `/packages` | admin | Create |
| PUT | `/packages/{id}` | admin | Edit |
| DELETE | `/packages/{id}` | admin | Delete |

### Settings & generator info
| Method | Path | Access | Description |
|---|---|---|---|
| GET / PUT | `/settings` | admin | Key-value settings |
| GET / PUT | `/generator-info` | admin | Company profile |

### Admin center
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/team` | admin | All users |
| POST | `/team` | admin | Create user (password ≥ 6 chars) |
| PUT | `/team/{id}/status` | admin | Activate/suspend (can't suspend self) |
| GET | `/audit?limit` | admin | Audit log (≤ 500) |
| POST | `/backup` | admin | SQLite snapshot (501 on Turso) |
| POST | `/sync/pull` | admin | MikroTik pull + cloud push |
| POST | `/sync/push` | admin | Accept full DB snapshot (upsert packages/customers) |
| GET / PUT | `/tower-connection` | admin | Saved MikroTik/OLT/SNMP settings |
| POST | `/tower-test` | admin | Live MikroTik connection test |

### Network tools
| Method | Path | Access | Description |
|---|---|---|---|
| POST | `/network/ping` | login | `{host, count}` → ICMP result |
| GET | `/signal-board` | login | Cached signals joined with customer names |
| GET | `/network/links` | login | List links |
| POST | `/network/links` | login | Add link (auto-pulls MikroTik subscribers when credentials present) |
| PUT | `/network/links/{id}` | login | Edit link (auto-pull) |
| DELETE | `/network/links/{id}` | admin | Delete link |
| POST | `/network/sync` | login | Pull subscribers from every saved MikroTik sector. On Render (cloud) returns `{cloud_mode: true, message}` (the tower EXE pulls locally and pushes via `/sync/push`); locally pulls directly + pushes to cloud |

### Reports
| Method | Path | Access | Description |
|---|---|---|---|
| GET | `/report?month&year` | login | Monthly finance summary (mirrors web `monthly_report`) |

### Devices (FCM, Phase 15-ready)
| Method | Path | Access | Description |
|---|---|---|---|
| POST | `/devices` | login | Register device push token `{token, platform}` |

### Printing
The existing web print URLs are reused by the app (opened via WebView /
`ACTION_VIEW`) — no JSON duplicates:

- `GET /print/receipt/{payment_id}` (80mm thermal)
- `GET /print/a4/receipt/{payment_id}`
- `GET /print/invoice/{invoice_id}`
- `GET /print/a4/invoice/{invoice_id}`

Excel exports likewise reuse the web admin routes
(`/api/export/excel`, `/api/export/payments/excel`).

---

## 4. Money-Math Rules (must stay in sync with `app.py`)

1. **New customer:** one current-month invoice, `total = package × months + previous_debt`; `paid_in_full=true` credits the debt portion.
2. **Renewal:** `payable = package × months + carried_debt` (carried = unpaid through current month). The carried debt is stored in `previous_debt` on the invoice.
3. **Debt rollover merge:** if a current-month invoice already exists (e.g. from quick-pay), renewal **updates** it to `new_total = package_price + carried_debt`, keeps the paid amount (capped at the new total), and sets `is_paid` accordingly — never inserts a duplicate.
4. **Quick-pay:** auto-creates the current invoice when none exists; payment clamps to the remaining amount.
5. **Payment edit/delete (admin):** invoice `paid_amount`/`is_paid` are recalculated from the payment deltas.
6. **Router sync:** `sync_customer_debt` is invoked after every money mutation.

---

## 5. Test Suite

```bat
set PYTHONIOENCODING=utf-8&& py test_mobile_api.py
```

Covers: error envelope, login success/wrong-password/suspended, token
validation, logout revocation, agent RBAC (403), dashboard KPIs,
create → quick-pay → renew money math (incl. debt merge), and the monthly
report shape. **53 assertions green.**