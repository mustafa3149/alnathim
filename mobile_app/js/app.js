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

function cacheClear(key) {
  try { localStorage.removeItem('alnathim_cache_' + key); } catch (e) {}
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
// api(path, body, method, timeoutMs) → abort after timeoutMs (0 = no timeout)
async function api(path, body, method, timeoutMs) {
  const url = API_BASE + path;
  const opts = {method: method || (body !== undefined && body !== null ? 'POST' : 'GET'), headers: getAuthHeaders()};
  if (body !== undefined && body !== null) {
    opts.body = JSON.stringify(body);
  }
  let timer = null;
  if (timeoutMs) {
    const ctrl = new AbortController();
    timer = setTimeout(() => ctrl.abort(), timeoutMs);
    opts.signal = ctrl.signal;
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
    const aborted = !!(timer && e && e.name === 'AbortError');
    return {ok: false, error: {code: aborted ? 'timeout' : 'network', message_ar: aborted ? 'انتهت المهلة — حاول مجدداً' : 'تعذر الاتصال بالسيرفر — تحقق من الإنترنت'}};
  } finally {
    if (timer) clearTimeout(timer);
  }
}

// ── Keep the server warm (Render free sleeps after ~15 min idle) ──
// While the app is open, ping a cheap endpoint every 10 minutes so page
// navigation never hits a 30-60s cold start.
function startKeepAlive() {
  if (window._keepAliveTimer) return;
  window._keepAliveTimer = setInterval(() => {
    try {
      fetch(API_BASE + '/auth/me', {headers: getAuthHeaders()}).catch(() => {});
    } catch (e) {}
  }, 10 * 60 * 1000);
}

// ── Seed the local cache right after login ──
// Fetches the key datasets in parallel so every page opens instantly from
// cache on first navigation (Blood-style: render local, refresh in bg).
async function warmupCache() {
  const tasks = [
    ['dashboard', '/dashboard/summary'],
    ['customers', '/customers'],
    ['debts', '/debts'],
    ['packages', '/packages'],
    ['payments', '/payments?limit=30'],
    ['tickets', '/tickets']
  ];
  await Promise.all(tasks.map(async ([key, path]) => {
    try {
      const d = await api(path);
      if (d.ok && d.data !== undefined) cacheSet(key, d.data);
    } catch (e) {}
  }));
  // Background warm-up: pull every customer's profile + current invoice +
  // history into the cache so tapping any customer opens INSTANTLY with all
  // details already on the phone (date, package, invoice, history...).
  warmCustomerDetails();
}

// True when a cache key exists and is newer than maxAgeMs.
function cacheFresh(key, maxAgeMs) {
  try {
    const raw = localStorage.getItem('alnathim_cache_' + key);
    if (!raw) return false;
    const o = JSON.parse(raw);
    return !!(o && o.ts && (Date.now() - o.ts) < maxAgeMs);
  } catch (e) { return false; }
}

// Fetch all customer profiles + current invoice + history in the background
// (limited concurrency, never blocks the UI) and cache them per customer.
async function warmCustomerDetails() {
  const custs = cacheGet('customers');
  const items = (custs && (custs.items || custs)) || [];
  if (!items.length) return;
  const TTL = 10 * 60 * 1000; // 10 minutes
  let idx = 0;
  async function worker() {
    while (idx < items.length) {
      const id = items[idx++].id;
      if (!cacheFresh('customer_' + id, TTL)) {
        const d = await api('/customers/' + id, null, null, 15000);
        if (d.ok && d.data && d.data.customer) cacheSet('customer_' + id, d.data.customer);
      }
      if (!cacheFresh('cinv_' + id, TTL)) {
        const inv = await api('/payments/current-invoice/' + id, null, null, 15000);
        if (inv.ok && inv.data) cacheSet('cinv_' + id, inv.data);
      }
      if (!cacheFresh('chist_' + id, TTL)) {
        const h = await api('/customers/' + id + '/history', null, null, 15000);
        if (h.ok && h.data) cacheSet('chist_' + id, h.data);
      }
    }
  }
  await Promise.all([worker(), worker(), worker()]);
}

// ── Silent background load (Blood-style: never block the page) ──
// Renders cached data instantly (if any), then refreshes quietly from the
// server. No spinner, no waiting — if there's nothing new, nothing happens.
async function silentLoad(key, path, renderFn) {
  const cached = cacheGet(key);
  if (cached) {
    try { renderFn(cached, true); } catch (e) {}
  }
  try {
    const d = await api(path);
    if (d.ok && d.data !== undefined) {
      cacheSet(key, d.data);
      try { renderFn(d.data, false); } catch (e) {}
    }
  } catch (e) {} // server offline → keep what we have, do nothing
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
    document.body.appendChild(t);
  }
  const err = type === 'error';
  t.className = err ? 'toast toast-error' : 'toast toast-ok';
  t.innerHTML = '<span class="toast-icon"></span><span>' + esc(msg) + '</span>';
  clearTimeout(t._timer);
  t.classList.add('show');
  t._timer = setTimeout(() => { t.classList.remove('show'); }, 3200);
}

// ── Bottom-sheet modal helpers ────────────────────────────────
// Show/clear an inline error INSIDE a sheet (right in front of the user).
function sheetError(id, msg) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = msg || '';
    el.style.display = msg ? 'block' : 'none';
  }
}
function clearSheetErrors() {
  document.querySelectorAll('.sheet-error').forEach(e => {
    e.textContent = ''; e.style.display = 'none';
  });
}
function openSheet(id) {
  const el = document.getElementById(id);
  if (el) {
    clearSheetErrors();
    el.classList.add('open');
    document.body.style.overflow = 'hidden';
    attachSheetDrag(el);
  }
}
function closeSheet(id) {
  const el = document.getElementById(id);
  if (el) { el.classList.remove('open'); document.body.style.overflow = ''; }
}
// Swipe the sheet DOWN to dismiss (bottom-sheet UX) — the drag handle and the
// whole sheet respond, so closing feels natural like native apps.
let _sheetDrag = null;
function attachSheetDrag(overlay) {
  const sheet = overlay.querySelector('.sheet');
  if (!sheet || sheet.dataset.drag) return;
  sheet.dataset.drag = '1';
  sheet.addEventListener('touchstart', e => {
    if (sheet.scrollTop > 0) return; // don't hijack scrolling the sheet body
    _sheetDrag = {id: overlay.id, startY: e.touches[0].clientY, dy: 0};
  }, {passive: true});
  sheet.addEventListener('touchmove', e => {
    if (!_sheetDrag || _sheetDrag.id !== overlay.id) return;
    _sheetDrag.dy = e.touches[0].clientY - _sheetDrag.startY;
    if (_sheetDrag.dy > 0) {
      sheet.style.transition = 'none';
      sheet.style.transform = 'translateY(' + Math.min(_sheetDrag.dy, 180) + 'px)';
    }
  }, {passive: true});
  sheet.addEventListener('touchend', () => {
    if (!_sheetDrag || _sheetDrag.id !== overlay.id) return;
    const dy = _sheetDrag.dy || 0;
    _sheetDrag = null;
    sheet.style.transition = '';
    sheet.style.transform = '';
    if (dy > 80) closeSheet(overlay.id);
  }, {passive: true});
}

