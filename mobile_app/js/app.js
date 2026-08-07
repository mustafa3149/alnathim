// ═══════════════════════════════════════════════════════════════
// AL-NATHIM Mobile — Shared JS (Blood-style hybrid shell)
// Local-first: pages render instantly from localStorage cache,
// server APIs refresh data in the background.
// ═══════════════════════════════════════════════════════════════

let API_BASE = '';

function getServerBase() {
  return localStorage.getItem('alnathim_server') || 'https://alnathim.onrender.com';
}

function refreshApiBase() {
  API_BASE = getServerBase().replace(/\/+$/, '') + '/api/mobile/v1';
}
refreshApiBase();

function getToken() {
  return localStorage.getItem('alnathim_token') || '';
}

function getUser() {
  try { return JSON.parse(localStorage.getItem('alnathim_user') || 'null'); }
  catch (e) { return null; }
}

function getAuthHeaders(extra) {
  const h = Object.assign({'Content-Type': 'application/json'}, extra || {});
  const t = getToken();
  if (t) h['Authorization'] = 'Bearer ' + t;
  return h;
}

// ── Cache helpers (localStorage) ──────────────────────────────
function cacheGet(key) {
  try {
    const raw = localStorage.getItem('alnathim_cache_' + key);
    if (!raw) return null;
    return JSON.parse(raw).data;
  } catch (e) { return null; }
}

function cacheSet(key, data) {
  try {
    localStorage.setItem('alnathim_cache_' + key, JSON.stringify({data: data, ts: Date.now()}));
  } catch (e) {}
}

// ── Retry helper (handles Render free-tier cold starts) ─────
// The first request after a long idle wakes the server (~30-60s);
// a transient failure on wake-up is retried automatically.
async function fetchWithRetry(url, opts, attempts, delayMs) {
  attempts = attempts || 3;
  delayMs = delayMs || 2500;
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fetch(url, opts);
    } catch (e) {
      lastErr = e;
      if (i < attempts - 1) await new Promise(r => setTimeout(r, delayMs));
    }
  }
  throw lastErr;
}

// ── API call with token + error envelope ──────────────────────
// api(path)                 → GET
// api(path, body)           → POST (body = object)
// api(path, body, 'PUT')    → PUT
// api(path, body, 'DELETE') → DELETE
async function api(path, body, method) {
  const url = API_BASE + path;
  const opts = {method: method || (body !== undefined && body !== null ? 'POST' : 'GET'), headers: getAuthHeaders()};
  if (body !== undefined && body !== null) {
    opts.body = JSON.stringify(body);
  }
  try {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => null);
    if (d === null) {
      return {ok: false, error: {code: 'bad_response', message_ar: 'استجابة غير متوقعة من السيرفر (' + r.status + ')'}};
    }
    if (!d.ok && d.error && (d.error.code === 'auth_invalid' || d.error.code === 'unauthorized')) {
      // Token expired/revoked — back to login
      localStorage.removeItem('alnathim_token');
      if (!window.location.pathname.endsWith('index.html') && !window.location.pathname.endsWith('/')) {
        window.location.href = 'index.html';
      }
    }
    return d;
  } catch (e) {
    return {ok: false, error: {code: 'network', message_ar: 'تعذر الاتصال بالسيرفر — تحقق من الإنترنت'}};
  }
}

// ── Load-from-cache-first helper ──────────────────────────────
async function loadCached(key, path, renderFn, onError) {
  const cached = cacheGet(key);
  if (cached) {
    try { renderFn(cached, true); } catch (e) {}
  }
  const d = await api(path);
  if (d.ok && d.data !== undefined) {
    cacheSet(key, d.data);
    try { renderFn(d.data, false); } catch (e) {}
  } else if (!cached && onError) {
    onError((d.error && d.error.message_ar) || 'فشل جلب البيانات');
  }
}

// ── Formatting helpers (same as web) ──────────────────────────
const ARABIC_DIGITS = ['٠', '١', '٢', '٣', '٤', '٥', '٦', '٧', '٨', '٩'];
function toArabicNum(n) {
  if (n === null || n === undefined || n === '') return '';
  return String(n).replace(/[0-9]/g, d => ARABIC_DIGITS[parseInt(d)]);
}
function formatDinar(amount) {
  const n = parseInt(amount) || 0;
  return toArabicNum(n.toLocaleString('en-US')) + ' د.ع';
}
function fmtMoney(amount) {
  return formatDinar(amount);
}

// ── Customer state helpers (match backend payload) ────────────
function customerState(c) {
  const st = String(c.subscription_status || c.status || 'active');
  if (st === 'suspended') return 'suspended';
  if (c.is_active === false || c.is_active === 0 || c.is_active === '0') return 'inactive';
  if (st === 'expired') return 'expired';
  return 'active';
}
const STATE_LABEL = {active: 'نشط', inactive: 'غير مفعل', expired: 'منتهي', suspended: 'موقوف'};
const STATE_DOT = {active: 'dot-active', inactive: 'dot-suspended', expired: 'dot-expired', suspended: 'dot-suspended'};
function stateText(s) { return STATE_LABEL[s] || '—'; }
function stateDot(s) { return '<span class="dot ' + (STATE_DOT[s] || 'dot-suspended') + '"></span>'; }

// Escape user content before injecting into innerHTML (XSS-safe)
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ── Guard: redirect to login if no token ──────────────────────
function requireAuth() {
  if (!getToken()) {
    window.location.href = 'index.html';
    return false;
  }
  return true;
}

// ── Logout ────────────────────────────────────────────────────
async function logout() {
  await api('/auth/logout', {});
  localStorage.removeItem('alnathim_token');
  localStorage.removeItem('alnathim_user');
  window.location.href = 'index.html';
}

// ── Toast ─────────────────────────────────────────────────────
function showToast(msg, type) {
  let t = document.getElementById('toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'toast';
    t.style.cssText = 'position:fixed;bottom:90px;left:50%;transform:translateX(-50%);z-index:9999;padding:10px 18px;border-radius:12px;font-size:13px;font-weight:700;box-shadow:0 4px 20px rgba(0,0,0,.4);transition:opacity .2s;';
    document.body.appendChild(t);
  }
  t.style.background = type === 'error' ? '#ef4444' : '#fafafa';
  t.style.color = type === 'error' ? '#fff' : '#0a0a0a';
  t.textContent = msg;
  clearTimeout(t._timer);
  t.style.opacity = '1';
  t._timer = setTimeout(() => { t.style.opacity = '0'; setTimeout(() => t.remove(), 300); }, 3000);
}

// ── Bottom-sheet modal helpers ────────────────────────────────
function openSheet(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.add('open'); document.body.style.overflow = 'hidden'; }
}
function closeSheet(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.remove('open'); document.body.style.overflow = ''; }
}

