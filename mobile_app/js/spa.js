// ═══════════════════════════════════════════════════════════
// AL-NATHIM Mobile — SPA controller
// One page, multiple sections — switching is INSTANT (like the
// Blood app). The shell never reloads; only the active section
// changes. Data paints from cache instantly, then refreshes
// silently in the background.
// ═══════════════════════════════════════════════════════════

// ── App entry (after login / session restore) ──
function enterApp() {
  document.getElementById('loginView').classList.add('hidden');
  document.getElementById('appShell').classList.remove('hidden');
  document.getElementById('loginCard').classList.remove('hidden');
  document.getElementById('waitingCard').classList.add('hidden');
  stopPolling();
  refreshApiBase();
  startKeepAlive();
  warmupCache();
  const u = getUser();
  IS_ADMIN = !!(u && u.role === 'admin');
  goTo('/view/dashboard');
}

// ── Router (hash-based: back button and hardware back work) ──
const VIEWS = {
  dashboard: { title: 'اللوحة', load: loadDashboard },
  customers: { title: 'العملاء', load: loadCustomers },
  billing:   { title: 'الفواتير', load: loadBilling },
  debts:     { title: 'الديون', load: loadDebts },
  more:      { title: 'المزيد', load: loadMore },  report: { title: 'التقرير', load: loadReportSection },  reminders: { title: 'التذكيرات', load: loadReminders, sub: true },  expenses: { title: 'المصاريف', load: loadExpenses, sub: true },  tickets: { title: 'التذاكر', load: loadTickets, sub: true },  packages: { title: 'الباقات', load: loadPackages, sub: true },  accounts: { title: 'الشركات', load: loadAccountsManage, sub: true },  generator: { title: 'بيانات الشركة', load: loadGenerator, sub: true },  team: { title: 'الفريق', load: loadTeam, sub: true },  audit: { title: 'سجل التدقيق', load: loadAudit, sub: true },  backup: { title: 'النسخ الاحتياطي', load: loadBackup, sub: true }
};
let currentView = 'dashboard';
let currentCustomerId = null;
let IS_ADMIN = false;

function goTo(hash) {
  if ((location.hash || '').replace(/^#/, '') === hash) applyHash(hash);
  else location.hash = hash;
}
function applyHash(hash) {
  hash = (hash || '').replace(/^#/, '');
  if (hash.indexOf('/customer/') === 0) {
    applyCustomer(decodeURIComponent(hash.split('/')[2] || ''));
  } else {
    applyView(hash.replace('/view/', ''));
  }
}
function applyView(name) {
  if (!VIEWS[name]) name = 'dashboard';
  currentView = name;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const view = document.getElementById('view-' + name);
  if (view) view.classList.add('active');
  document.querySelectorAll('.bottom-nav a[data-nav]').forEach(a =>
    a.classList.toggle('active', !VIEWS[name].sub && a.dataset.nav === name));
  document.getElementById('backBtn').classList.toggle('hidden', !!VIEWS[name].sub);
  document.getElementById('topTitle').textContent = VIEWS[name].title;
  document.getElementById('topLabel').textContent = '';
  closeSheets();
  const loader = VIEWS[name].load;
  if (loader) loader();
  window.scrollTo(0, 0);
}
function applyCustomer(id) {
  currentCustomerId = String(id || '');
  currentView = 'customer';
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  const view = document.getElementById('view-customer');
  if (view) view.classList.add('active');
  document.querySelectorAll('.bottom-nav a[data-nav]').forEach(a => a.classList.remove('active'));
  document.getElementById('backBtn').classList.remove('hidden');
  document.getElementById('topTitle').textContent = 'المشترك';
  document.getElementById('topLabel').textContent = '';
  closeSheets();
  document.getElementById('mainBox').innerHTML = '';
  document.querySelectorAll('#view-customer .admin-only').forEach(el => el.classList.toggle('hidden', !IS_ADMIN));
  loadCustomer();
  window.scrollTo(0, 0);
}
function goBack() { history.back(); }
function closeSheets() {
  ['addSheet', 'paySheet', 'renewSheet', 'regSheet'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('open');
  });
  document.body.style.overflow = '';
}

// ── DASHBOARD ──────────────────────────────────────────────
function loadDashboard() {
  const u = getUser();
  if (u) document.getElementById('topLabel').textContent =
    u.role === 'admin' ? 'مدير' : (u.full_name || u.username || '');

  function renderSummary(d) {
    document.getElementById('statIncome').textContent = formatDinar(d.expected_income ?? d.total_expected ?? 0);
    document.getElementById('statDebt').textContent = formatDinar(d.total_debt ?? 0);
    document.getElementById('statToday').textContent = formatDinar(d.collected_today ?? 0);
    document.getElementById('statActive').textContent = toArabicNum(d.active_customers ?? d.total_customers ?? 0);

    const expBox = document.getElementById('expiringBox');
    document.getElementById('expLabel').textContent = 'المنتهيون هذا الأسبوع (' + toArabicNum(d.expiring_this_week ?? 0) + ')';
    const exp = d.expiring_customers || [];
    if (!exp.length) {
      expBox.innerHTML = '<div class="empty"><div class="big">✅</div>لا أحد ينتهي هذا الأسبوع</div>';
    } else {
      expBox.innerHTML = '<div class="list-card">' + exp.map(e =>
        '<a class="row" href="customer-detail.html?id=' + e.id + '">' +
          stateDot('expired') +
          '<div class="row-main">' +
            '<div class="row-title">' + esc(e.name) + '</div>' +
            '<div class="row-sub">' + esc(e.package_name || '') + ' · ينتهي ' + esc(String(e.renewal_date || '').slice(0, 10)) + '</div>' +
          '</div>' +
          '<span class="menu-arrow">‹</span>' +
        '</a>').join('') + '</div>';
    }

    const payBox = document.getElementById('payBox');
    const pays = d.recent_payments || [];
    if (!pays.length) {
      payBox.innerHTML = '<div class="empty"><div class="big">💳</div>لا مدفوعات بعد</div>';
    } else {
      payBox.innerHTML = '<div class="list-card">' + pays.map(p =>
        '<div class="row">' +
          '<div class="row-main">' +
            '<div class="row-title">' + (p.customer_name ? esc(p.customer_name) : 'زبون #' + p.customer_id) + '</div>' +
            '<div class="row-sub">' + esc(p.payment_date || '') + ' · ' + esc(p.payment_method || '') +
          (p.collected_by_name ? ' · استلمه: ' + esc(p.collected_by_name) : '') + '</div>' +
          '</div>' +
          '<div class="row-end" style="text-align:left;">' +
          '<div style="font-weight:800;color:var(--active);">' + formatDinar(p.amount || 0) + '</div>' +
          (IS_ADMIN ? '<div style="display:flex;gap:6px;margin-top:6px;">' +
            '<button class="btn btn-sm" onclick="editPayment(' + p.id + ')">تعديل</button>' +
            '<button class="btn btn-sm" style="color:var(--expired);" onclick="deletePayment(' + p.id + ')">حذف</button>' +
          '</div>' : '') +
        '</div>' +
        '</div>').join('') + '</div>';
    }
  }

  // Cache-first (Blood-style): paints instantly, refreshes quietly.
  silentLoad('dashboard', '/dashboard/summary', renderSummary);
}
// ── CUSTOMERS ──────────────────────────────────────────────
let allCustomers = [];
let debtMap = {};
let currentStatus = 'all';

function setStatus(btn, s) {
  currentStatus = s;
  document.querySelectorAll('#statusChips .chip').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderFiltered();
}

function renderFiltered() {
  const q = document.getElementById('searchInput').value.trim().toLowerCase();
  let list = allCustomers;
  if (q) {
    list = list.filter(c => (c.name || '').toLowerCase().includes(q) || (c.phone || '').includes(q));
  }
  if (currentStatus === 'active') list = list.filter(c => customerState(c) === 'active');
  else if (currentStatus === 'expired') list = list.filter(c => customerState(c) === 'expired' || customerState(c) === 'inactive');
  else if (currentStatus === 'debt') list = list.filter(c => (debtMap[c.id] || 0) > 0);

  document.getElementById('topLabel').textContent = toArabicNum(list.length) + ' مشترك';
  const box = document.getElementById('customerList');
  if (!list.length) {
    box.innerHTML = '<div class="empty"><div class="big">👥</div>لا يوجد عملاء مطابقون</div>';
    return;
  }
  box.innerHTML = '<div class="list-card">' + list.map(c => {
    const st = customerState(c);
    const debt = debtMap[c.id] || 0;
    return '<a class="row" href="customer-detail.html?id=' + c.id + '">' +
      stateDot(st) +
      '<div class="row-main">' +
        '<div class="row-title">' + esc(c.name) + '</div>' +
        '<div class="row-sub">' + esc(c.phone || '') + ' · ' + esc(c.package_name || '') +
        (c.port_number ? ' · <span class="badge badge-ok">كابينة ' + esc(c.cabinet_number || c.fat_id || '') + ' / ' + c.port_number + '</span>' : '') + '</div>' +
      '</div>' +
      '<div class="row-end">' +
        '<div style="font-size:11px;color:var(--muted);font-weight:700;">' + stateText(st) + '</div>' +
        (debt > 0 ? '<div style="font-size:11px;font-weight:800;color:var(--expired);">' + formatDinar(debt) + '</div>' : '') +
      '</div>' +
      '<span class="menu-arrow">‹</span>' +
    '</a>';
  }).join('') + '</div>';
}

function renderCached(list) { allCustomers = list; renderFiltered(); }

async function loadCustomers() {
  const cached = cacheGet('customers');
  if (cached && (cached.items || cached)) renderCached(cached.items || cached);
  const d = await api('/customers');
  const dd = await api('/debts');
  if (d.ok) {
    const items = d.data.items || [];
    cacheSet('customers', d.data);
    if (dd.ok) {
      debtMap = {};
      (dd.data.items || []).forEach(x => { debtMap[x.customer_id] = x.total_debt || 0; });
    }
    allCustomers = items;
    renderFiltered();
  }
}

async function addCustomer() {
  const name = document.getElementById('cName').value.trim();
  if (!name) { showToast('أدخل اسم المشترك', 'error'); return; }
  const body = {
    name: name,
    phone: document.getElementById('cPhone').value.trim(),
    package_name: document.getElementById('cPackage').value.trim(),
    package_price: parseInt(document.getElementById('cPrice').value) || 0,
    duration_months: Math.max(1, parseInt(document.getElementById('cMonths').value) || 1),
    previous_debt: parseInt(document.getElementById('cDebt').value) || 0,
    address: document.getElementById('cAddress').value.trim(),
    region: document.getElementById('cRegion').value.trim(),
    notes: document.getElementById('cNotes').value.trim(),
    cabinet_number: document.getElementById('cCabinet') ? document.getElementById('cCabinet').value.trim() : '',
    port_number: document.getElementById('cPort') ? (document.getElementById('cPort').value || null) : null
  };
  const btn = document.getElementById('addCustBtn');
  btn.textContent = '⏳ جاري الحفظ...'; btn.disabled = true;
  const d = await api('/customers', body);
  btn.textContent = 'حفظ المشترك'; btn.disabled = false;
  if (d.ok) {
    closeSheet('addSheet');
    cacheClear('customers'); cacheClear('debts'); cacheClear('dashboard');
    showToast('تمت إضافة المشترك ✓');
    ['cName','cPhone','cPackage','cAddress','cRegion','cNotes','cCabinet','cPort'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('cPrice').value = '25000';
    document.getElementById('cMonths').value = '1';
    document.getElementById('cDebt').value = '0';
    loadCustomers();
  } else {
    showToast((d.error && d.error.message_ar) || 'فشل الإضافة', 'error');
  }
}

// ── BILLING ────────────────────────────────────────────────
function loadBilling() {
  const now = new Date();
  document.getElementById('monthSel').value = String(now.getMonth() + 1);
  document.getElementById('yearSel').value = String(now.getFullYear());
  loadInvoices();
}

function loadInvoices() {
  const month = document.getElementById('monthSel').value;
  const year = document.getElementById('yearSel').value;
  const box = document.getElementById('billingList');
  const cacheKey = 'invoices_' + month + '_' + year;

  function render(data) {
    const items = (data && data.items) || [];
    let collected = 0, remaining = 0;
    items.forEach(inv => {
      collected += inv.paid_amount || 0;
      remaining += (inv.total_amount || 0) - (inv.paid_amount || 0);
    });
    document.getElementById('sumCollected').textContent = formatDinar(collected);
    document.getElementById('sumRemaining').textContent = formatDinar(remaining);
    if (!items.length) {
      box.innerHTML = '<div class="empty"><div class="big">🧾</div>لا توجد فواتير لهذا الشهر</div>';
      return;
    }
    box.innerHTML = '<div class="list-card">' + items.map(inv => {
      const rem = (inv.total_amount || 0) - (inv.paid_amount || 0);
      return '<a class="row" href="customer-detail.html?id=' + inv.customer_id + '">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(inv.customer_name || 'زبون #' + inv.customer_id) + '</div>' +
          '<div class="row-sub">' + esc(inv.package_name || '') + ' · ' + toArabicNum(inv.month) + '/' + toArabicNum(inv.year) + '</div>' +
        '</div>' +
        '<div class="row-end" style="text-align:left;">' +
          '<div style="font-weight:800;">' + formatDinar(inv.total_amount || 0) + '</div>' +
          (rem > 0
            ? '<div style="font-size:11px;font-weight:800;color:var(--expired);">متبقي ' + formatDinar(rem) + '</div>'
            : '<div style="font-size:11px;font-weight:800;color:var(--active);">مدفوعة ✓</div>') +
        '</div>' +
        '<span class="menu-arrow">‹</span>' +
      '</a>';
    }).join('') + '</div>';
  }

  const cached = cacheGet(cacheKey);
  if (cached) render(cached);
  api('/invoices?month=' + month + '&year=' + year).then(d => {
    if (d.ok) {
      cacheSet(cacheKey, d.data);
      render(d.data);
    }
  });
}
// ── DEBTS ──────────────────────────────────────────────────
function renderDebts(data) {
  const items = (data && data.items) || [];
  let total = 0;
  items.forEach(x => total += x.total_debt || 0);
  document.getElementById('topLabel').textContent = formatDinar(total);
  const box = document.getElementById('debtList');
  if (!items.length) {
    box.innerHTML = '<div class="empty"><div class="big">✅</div>لا توجد ديون — كل شيء مدفوع</div>';
    return;
  }
  box.innerHTML = items.map(x => {
    const wa = (x.phone || '').replace(/[^0-9]/g, '');
    return '<div class="card" style="padding:14px;margin-bottom:10px;">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;">' +
        '<div style="flex:1;min-width:0;">' +
          '<div style="font-size:14px;font-weight:700;">' + esc(x.name) + '</div>' +
          '<div style="font-size:11px;color:var(--muted);margin-top:2px;">' + esc(x.package_name || '') +
            (x.region ? ' · ' + esc(x.region) : '') + ' · ' + toArabicNum(x.unpaid_count || 0) + ' فاتورة' +
          '</div>' +
        '</div>' +
        '<div style="text-align:left;flex-shrink:0;">' +
          '<div style="font-size:16px;font-weight:800;color:var(--expired);">' + formatDinar(x.total_debt || 0) + '</div>' +
        '</div>' +
      '</div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px;">' +
        '<a class="btn" style="text-decoration:none;font-size:13px;" href="customer-detail.html?id=' + x.customer_id + '">تسديد</a>' +
        (wa ? '<a class="wa-btn" style="margin-top:0;text-decoration:none;" href="https://wa.me/' + wa + '?text=' + encodeURIComponent('عزيزي ' + x.name + '، لديك مبلغ مستحق ' + (x.total_debt || 0) + ' د.ع — يرجى التسديد.') + '">واتساب تذكير</a>' : '') +
      '</div>' +
    '</div>';
  }).join('');
}
function loadDebts() {
  silentLoad('debts', '/debts', renderDebts);
}

// ── MORE ───────────────────────────────────────────────────
function loadMore() {
  const u = getUser();
  if (u) document.getElementById('topLabel').textContent =
    (u.account_name ? u.account_name + ' · ' : '') +
    (u.role === 'admin' ? 'مدير' : (u.full_name || u.username || ''));
  const isAdmin = !!(u && u.role === 'admin');
  const isOwner = !!(u && u.role === 'admin' && (u.account_id || 0) === 1);
  document.querySelectorAll('#view-more .admin-only').forEach(el => el.classList.toggle('hidden', !isAdmin));
  document.querySelectorAll('#view-more .owner-only').forEach(el => el.classList.toggle('hidden', !isOwner));

}
function loadReport() {
  const month = document.getElementById('rMonth').value;
  const year = document.getElementById('rYear').value;
  const cached = cacheGet('report_' + month + '_' + year);
  if (cached) renderReport(cached);
  api('/report?month=' + month + '&year=' + year).then(d => {
    if (d.ok) {
      cacheSet('report_' + month + '_' + year, d.data);
      renderReport(d.data);
    }
  });
}
function renderReport(r) {
  const box = document.getElementById('reportBox');
  box.innerHTML =
      '<div class="kpi-grid">' +
        '<div class="kpi"><div class="k-label">المتوقع</div><div class="k-value">' + formatDinar(r.expected || 0) + '</div></div>' +
        '<div class="kpi"><div class="k-label">المحصل</div><div class="k-value" style="color:var(--active);">' + formatDinar(r.collected || 0) + '</div></div>' +
        '<div class="kpi"><div class="k-label">المصاريف</div><div class="k-value" style="color:var(--expired);">' + formatDinar(r.expenses_total || 0) + '</div></div>' +
        '<div class="kpi"><div class="k-label">صافي الربح</div><div class="k-value">' + formatDinar(r.net_profit || 0) + '</div></div>' +
        '<div class="kpi"><div class="k-label">المتبقي</div><div class="k-value" style="color:var(--expired);">' + formatDinar(r.remaining || 0) + '</div></div>' +
        '<div class="kpi"><div class="k-label">إجمالي الديون</div><div class="k-value" style="color:var(--expired);">' + formatDinar(r.total_debt || 0) + '</div></div>' +
      '</div>' +
      '<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--muted);margin-top:10px;">' +
        '<span>فواتير: ' + toArabicNum(r.total_invoices || 0) + '</span>' +
        '<span>مدفوعة: ' + toArabicNum(r.paid_count || 0) + '</span>' +
        '<span>غير مدفوعة: ' + toArabicNum(r.unpaid_count || 0) + '</span>' +
      '</div>' +
      (r.collectors && r.collectors.length ? '<div class="section-label">المحصلون</div><div class="list-card">' +
        r.collectors.map(c => '<div class="row"><div class="row-main"><div class="row-title">' + esc(c.name) + '</div><div class="row-sub">' + toArabicNum(c.payment_count || 0) + ' دفعة</div></div><div class="row-end" style="font-weight:800;color:var(--active);">' + formatDinar(c.total || 0) + '</div></div>').join('') +
        '</div>' : '');
}
function loadPayments() {
  const box = document.getElementById('paymentsBox');
  function render(data) {
    const items = (data && data.items) || [];
    if (!items.length) {
      box.innerHTML = '<div class="empty"><div class="big">💳</div>لا مدفوعات بعد</div>';
      return;
    }
    box.innerHTML = '<div class="list-card">' + items.map(p =>
      '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(p.customer_name || 'زبون #' + p.customer_id) + '</div>' +
          '<div class="row-sub">' + esc(p.payment_date || '') + ' · ' + esc(p.payment_method || '') +
          (p.collected_by_name ? ' · استلمه: ' + esc(p.collected_by_name) : '') + '</div>' +
        '</div>' +
        '<div class="row-end" style="text-align:left;">' +
          '<div style="font-weight:800;color:var(--active);">' + formatDinar(p.amount || 0) + '</div>' +
          (IS_ADMIN ? '<div style="display:flex;gap:6px;margin-top:6px;">' +
            '<button class="btn btn-sm" onclick="editPayment(' + p.id + ')">تعديل</button>' +
            '<button class="btn btn-sm" style="color:var(--expired);" onclick="deletePayment(' + p.id + ')">حذف</button>' +
          '</div>' : '') +
        '</div>' +
      '</div>').join('') + '</div>';
  }
  const cached = cacheGet('payments');
  if (cached) render(cached);
  api('/payments?limit=30').then(d => {
    if (d.ok) {
      cacheSet('payments', d.data);
      render(d.data);
    }
  });
}
function toggleServer() {
  const panel = document.getElementById('serverPanel');
  panel.classList.toggle('hidden');
  if (!panel.classList.contains('hidden')) {
    document.getElementById('serverUrl').value = getServerBase();
  }
}
function saveServer() {
  const url = document.getElementById('serverUrl').value.trim().replace(/\/+$/, '');
  if (!url) return;
  localStorage.setItem('alnathim_server', url);
  refreshApiBase();
  showToast('تم حفظ عنوان السيرفر ✓');
}
// ── CUSTOMER DETAIL ────────────────────────────────────────
let cust = null;
let currentInvoice = null;

function loadCustomer() {
  const cid = currentCustomerId;
  if (!cid) {
    document.getElementById('mainBox').innerHTML = '<div class="empty">معرف المشترك غير موجود</div>';
    return;
  }
  const cached = cacheGet('customer_' + cid);
  if (cached) renderCustomer(cached);
  api('/customers/' + cid).then(d => {
    if (d.ok && d.data && d.data.customer) {
      cacheSet('customer_' + cid, d.data.customer);
      renderCustomer(d.data.customer);
    }
  });
}

function renderCustomer(c) {
  cust = c;
  document.getElementById('topTitle').textContent = c.name || 'المشترك';
  const st = customerState(c);
  const waPhone = (c.whatsapp_phone || c.phone || '').replace(/[^0-9]/g, '');

  let html = '';
  html += '<div class="card card-pad" style="display:flex;align-items:center;gap:12px;margin-top:14px;">' +
    stateDot(st) +
    '<div style="flex:1;">' +
      '<div style="font-size:17px;font-weight:800;">' + esc(c.name || '—') + '</div>' +
      '<div style="font-size:12px;color:var(--muted);margin-top:2px;">' + esc(c.package_name || '') + ' · ' + stateText(st) + '</div>' +
    '</div>' +
    (waPhone ? '<a href="https://wa.me/' + waPhone + '" class="btn btn-sm" style="text-decoration:none;">واتساب</a>' : '') +
  '</div>';

  html += '<div class="section-label">فاتورة الشهر الحالي</div>';
  html += '<div id="invBox" class="card card-pad" style="margin-top:8px;"></div>';

  html += '<div class="section-label">البيانات</div>';
  html += '<div class="detail-card">';
  const rows = [
    ['الهاتف', c.phone], ['هاتف ٢', c.phone2], ['الباقة', c.package_name],
    ['سعر الباقة', c.package_price ? formatDinar(c.package_price) : ''],
    ['المنطقة', c.region], ['العنوان', c.address],
    ['اسم المستخدم', c.username], ['كلمة المرور', c.password],
    ['IP', c.ip_address], ['نوع الجهاز', c.device_type],
    ['الكابينة / المنفذ', c.port_number ? ((c.cabinet_number || c.fat_id || '') + ' / ' + c.port_number) : ''],
    ['تاريخ الاشتراك', c.subscription_date], ['تاريخ التجديد', c.renewal_date],
    ['دين سابق', c.previous_debt ? formatDinar(c.previous_debt) : '']
  ];
  rows.forEach(r => {
    if (r[1] !== undefined && r[1] !== null && String(r[1]).trim() !== '') {
      html += '<div class="detail-row"><span class="d-label">' + r[0] + '</span><span class="d-value">' + esc(r[1]) + '</span></div>';
    }
  });
  html += '</div>';

  html += '<div class="section-label">السجل</div>';
  html += '<div id="historyBox"></div>';

  document.getElementById('mainBox').innerHTML = html;
  loadInvoice(c);
  loadHistory(c);
}

function loadInvoice(c) {
  api('/payments/current-invoice/' + c.id).then(d => {
    const box = document.getElementById('invBox');
    if (!box) return;
    if (!d.ok || !d.data || !d.data.invoice) {
      box.innerHTML = '<div style="text-align:center;color:var(--muted);font-size:13px;padding:6px 0;">لا توجد فاتورة لهذا الشهر</div>' +
        '<button class="btn-primary" style="margin-top:12px;" onclick="openPaySheet()">إنشاء وتسديد</button>';
      currentInvoice = null;
      return;
    }
    const inv = d.data.invoice;
    currentInvoice = inv;
    const remaining = (inv.total_amount || 0) - (inv.paid_amount || 0);
    box.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;">' +
        '<div><div style="font-size:12px;color:var(--muted);">' + toArabicNum(inv.month) + '/' + toArabicNum(inv.year) + ' · ' + esc(inv.package_name || '') + '</div>' +
        '<div style="font-size:18px;font-weight:800;margin-top:2px;">' + formatDinar(inv.total_amount || 0) + '</div></div>' +
        '<div style="text-align:left;"><div class="badge ' + (remaining <= 0 ? 'badge-ok' : 'badge-bad') + '">' + (remaining <= 0 ? 'مدفوعة ✓' : 'متبقي') + '</div>' +
        (remaining > 0 ? '<div style="font-size:13px;font-weight:800;color:var(--expired);margin-top:4px;">' + formatDinar(remaining) + '</div>' : '') + '</div>' +
      '</div>' +
      (remaining > 0
        ? '<button class="btn-primary" style="margin-top:14px;" onclick="openPaySheet()">تسديد (' + formatDinar(remaining) + ')</button>'
        : '<div style="text-align:center;color:var(--active);font-size:13px;font-weight:700;margin-top:12px;">مكتملة ✓</div>') +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px;">' +
        '<button class="btn" onclick="openRenewSheet()">تجديد</button>' +
        '<button class="btn" onclick="toggleCustomer()">' + (c.is_active ? 'إيقاف' : 'تفعيل') + '</button>' +
      '</div>';
  });
}

function loadHistory(c) {
  api('/customers/' + c.id + '/history').then(d => {
    const box = document.getElementById('historyBox');
    if (!box) return;
    if (!d.ok) return;
    const invs = d.data.invoices || [];
    const pays = d.data.payments || [];
    let h = '';
    if (pays.length) {
      h += '<div class="section-label" style="margin:8px 0;">الدفعات</div><div class="list-card">';
      h += pays.map(p =>
        '<div class="row"><div class="row-main"><div class="row-title">' + (p.customer_name ? esc(p.customer_name) : '') + '</div>' +
        '<div class="row-sub">' + esc(p.payment_date || '') + ' · ' + esc(p.payment_method || '') + (p.collected_by_name ? ' · استلمه: ' + esc(p.collected_by_name) : '') + '</div></div>' +
        '<div class="row-end" style="font-weight:800;color:var(--active);">' + formatDinar(p.amount || 0) + '</div></div>'
      ).join('');
      h += '</div>';
    }
    if (invs.length) {
      h += '<div class="section-label" style="margin:8px 0;">الفواتير</div><div class="list-card">';
      h += invs.map(inv =>
        '<div class="row"><div class="row-main"><div class="row-title">' + toArabicNum(inv.month) + '/' + toArabicNum(inv.year) + ' · ' + esc(inv.package_name || '') + '</div>' +
        '<div class="row-sub">' + (inv.is_paid ? 'مدفوعة ✓' : 'متبقي ' + formatDinar((inv.total_amount || 0) - (inv.paid_amount || 0))) + '</div></div>' +
        '<div class="row-end" style="font-weight:700;">' + formatDinar(inv.total_amount || 0) + '</div></div>'
      ).join('');
      h += '</div>';
    }
    if (!h) h = '<div class="empty">لا يوجد سجل بعد</div>';
    box.innerHTML = h;
  });
}

// ── Quick pay ──
function openPaySheet() {
  if (currentInvoice && currentInvoice.paid_amount > 0) {
    document.getElementById('payInvoiceBox').innerHTML =
      '<div style="font-size:13px;color:var(--muted);">مدفوع حتى الآن: <b style="color:var(--active);">' + formatDinar(currentInvoice.paid_amount) + '</b></div>';
  } else {
    document.getElementById('payInvoiceBox').innerHTML = '';
  }
  document.getElementById('payAmount').value = '';
  document.querySelectorAll('#amountSeg button').forEach(b => b.classList.remove('active'));
  openSheet('paySheet');
}
function pickAmount(btn, v) {
  document.querySelectorAll('#amountSeg button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  if (v > 0) document.getElementById('payAmount').value = v;
  else document.getElementById('payAmount').value = '';
}
async function submitPay() {
  const amountRaw = document.getElementById('payAmount').value.trim();
  const amount = amountRaw === '' ? 0 : parseInt(amountRaw) || 0;
  const btn = document.getElementById('payBtn');
  btn.textContent = '⏳ جاري الدفع...'; btn.disabled = true;
  const d = await api('/quick-pay/' + currentCustomerId, {
    amount: amount,
    payment_method: document.getElementById('payMethod').value
  });
  btn.textContent = 'تأكيد الدفع'; btn.disabled = false;
  if (d.ok) {
    closeSheet('paySheet');
    showToast('تم استلام ' + formatDinar(d.data.amount) + ' ✓');
    loadCustomer();
  } else {
    showToast((d.error && d.error.message_ar) || 'فشل الدفع', 'error');
  }
}

// ── Renew ──
function openRenewSheet() {
  document.getElementById('renewMonths').value = '1';
  const cur = (cust && cust.package_name) || '';
  fillPackagesSelect(document.getElementById('renewPackage'), cur);
  openSheet('renewSheet');
}
async function submitRenew() {
  const months = Math.max(1, parseInt(document.getElementById('renewMonths').value) || 1);
  const sel = document.getElementById('renewPackage');
  const opt = sel.options[sel.selectedIndex];
  const newPkg = (opt && opt.value) ? opt.value : ((cust && cust.package_name) || '');
  const newPrice = (opt && opt.dataset.price) ? opt.dataset.price : ((cust && cust.package_price) || 0);
  const btn = document.getElementById('renewBtn');
  btn.textContent = '⏳ جاري التجديد...'; btn.disabled = true;
  if (newPkg && newPkg !== (cust && cust.package_name)) {
    const u = await api('/customers/' + currentCustomerId, { package_name: newPkg, package_price: newPrice }, 'PUT');
    if (!u.ok) {
      btn.textContent = 'تأكيد التجديد'; btn.disabled = false;
      showToast((u.error && u.error.message_ar) || 'فشل تحديث الباقة', 'error');
      return;
    }
  }
  const d = await api('/customers/' + currentCustomerId + '/renew', {months: months});
  btn.textContent = 'تأكيد التجديد'; btn.disabled = false;
  if (d.ok) {
    closeSheet('renewSheet');
    showToast('تم التجديد ' + toArabicNum(months) + ' شهر ✓');
    loadCustomer();
  } else {
    showToast((d.error && d.error.message_ar) || 'فشل التجديد', 'error');
  }
}

// ── Toggle active ──
async function toggleCustomer() {
  if (!confirm('تأكيد تغيير حالة المشترك؟')) return;
  const d = await api('/customers/' + currentCustomerId + '/toggle', {});
  if (d.ok) { showToast('تم التغيير ✓'); loadCustomer(); }
  else showToast((d.error && d.error.message_ar) || 'فشل التغيير', 'error');
}
// ── LOGIN ──────────────────────────────────────────────────
let pollTimer = null;
let pollCount = 0;
let pendingUser = '';
let pendingPass = '';

function showError(msg) {
  const box = document.getElementById('errorBox');
  if (!box) return;
  box.textContent = msg;
  box.style.display = 'block';
  setTimeout(() => { box.style.display = 'none'; }, 6000);
}
function showWaiting(msg) {
  document.getElementById('loginCard').classList.add('hidden');
  document.getElementById('restoreBox').classList.add('hidden');
  document.getElementById('waitingCard').classList.remove('hidden');
  document.getElementById('waitingMsg').innerHTML = msg.replace(/\n/g, '<br>');
}
function cancelWaiting() {
  stopPolling();
  document.getElementById('waitingCard').classList.add('hidden');
  document.getElementById('loginCard').classList.remove('hidden');
}
function startPolling() {
  stopPolling();
  pollCount = 0;
  pollTimer = setInterval(pollStatus, 10000);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}
async function pollStatus() {
  pollCount++;
  try {
    const r = await fetchWithRetry(API_BASE + '/auth/check-status', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: pendingUser, password: pendingPass})
    }, 2, 2500);
    const d = await r.json().catch(() => null);
    const state = d && d.data && d.data.state;
    if (state === 'approved') {
      stopPolling();
      localStorage.setItem('alnathim_token', d.data.token);
      localStorage.setItem('alnathim_user', JSON.stringify(d.data.user || {}));
      document.getElementById('waitingMsg').textContent = '✅ تمت الموافقة — جاري الدخول';
      setTimeout(enterApp, 600);
    } else if (state === 'pending') {
      document.getElementById('waitingMsg').textContent =
        'بانتظار موافقة المدير — سيتم الدخول تلقائياً (' + Math.round(pollCount * 10) + ' ثانية)';
    } else if (state === 'suspended' || state === 'expired' || state === 'invalid') {
      stopPolling();
      cancelWaiting();
      showError((d.data && d.data.error) || 'تعذر الدخول');
    } else if (state === 'busy') {
      document.getElementById('waitingMsg').textContent = 'طلبات كثيرة — إعادة المحاولة تلقائياً...';
    }
  } catch (e) {
    document.getElementById('waitingMsg').textContent = 'تعذر الاتصال بالخادم — جارٍ إعادة المحاولة...';
  }
}
async function doLogin() {
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const server = document.getElementById('serverBase').value.trim().replace(/\/+$/, '');
  if (!username || !password) return showError('أدخل اسم المستخدم وكلمة المرور');
  if (!server) return showError('أدخل عنوان السيرفر');
  localStorage.setItem('alnathim_server', server);
  refreshApiBase();
  const btn = document.getElementById('loginBtn');
  btn.textContent = '⏳ جاري الدخول...'; btn.disabled = true;
  try {
    const r = await fetchWithRetry(API_BASE + '/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username, password})
    }, 3, 2500);
    const d = await r.json().catch(() => null);
    btn.textContent = 'دخول'; btn.disabled = false;
    handleLoginResult(d, username, password);
  } catch (e) {
    btn.textContent = 'دخول'; btn.disabled = false;
    showError('تعذر الاتصال بالسيرفر — تأكد من الإنترنت ثم أعد المحاولة');
  }
}
function openRegSheet() {
  document.getElementById('rCompany').value = '';
  const rn = document.getElementById('rName');
  if (rn) rn.value = '';
  document.getElementById('rUsername').value = '';
  document.getElementById('rPhone').value = '';
  document.getElementById('rPassword').value = '';
  openSheet('regSheet');
}
function loadAccounts() {
  const sel = document.getElementById('accountId');
  if (!sel) return;
  fetchWithRetry(API_BASE + '/auth/accounts', {}, 2, 2500)
    .then(r => r.json().catch(() => null))
    .then(d => {
      const items = (d && d.data && d.data.items) || [];
      if (!items.length) {
        sel.innerHTML = '<option value="">لا توجد شركات بعد — سجّل شركتك</option>';
        return;
      }
      sel.innerHTML = '<option value="">اختر الشركة...</option>' + items.map(a =>
        '<option value="' + a.id + '">' + esc(a.name) + '</option>').join('');
    })
    .catch(() => {
      sel.innerHTML = '<option value="">تعذر تحميل الشركات</option>';
    });
}
function onAccountChange() {
  const accountId = document.getElementById('accountId').value;
  const sel = document.getElementById('username');
  if (!accountId) { sel.innerHTML = '<option value="">اختر الشركة أولاً...</option>'; return; }
  sel.innerHTML = '<option value="">جارِ التحميل...</option>';
  fetchWithRetry(API_BASE + '/auth/accounts/' + accountId + '/users', {}, 2, 2500)
    .then(r => r.json().catch(() => null))
    .then(d => {
      const items = (d && d.data && d.data.items) || [];
      sel.innerHTML = '<option value="">اختر المستخدم...</option>' + items.map(u =>
        '<option value="' + esc(u.username) + '">' + esc(u.full_name || u.username) + ' (' + esc(u.username) + ')</option>').join('');
    })
    .catch(() => {
      sel.innerHTML = '<option value="">تعذر تحميل المستخدمين</option>';
    });
}
function handleLoginResult(d, username, password) {
  if (d && d.ok && d.data && d.data.token) {
    localStorage.setItem('alnathim_token', d.data.token);
    localStorage.setItem('alnathim_user', JSON.stringify(d.data.user || {}));
    enterApp();
    return;
  }
  const code = d && d.error && d.error.code;
  const msg = (d && d.error && d.error.message_ar) || 'فشل الدخول';
  if (code === 'pending') {
    const hint = document.getElementById('loginHint');
    if (hint) hint.style.display = 'block';
    pendingUser = username; pendingPass = password;
    showWaiting('بانتظار موافقة المدير — سيتم الدخول تلقائياً فور الموافقة');
    startPolling();
  } else {
    showError(msg);
  }
}
async function doRegister() {
  const company_name = document.getElementById('rCompany').value.trim();
  const username = document.getElementById('rUsername').value.trim();
  const password = document.getElementById('rPassword').value;
  const phone = document.getElementById('rPhone').value.trim();
  if (!company_name || !username || !password) { showToast('أدخل اسم الشركة والمستخدم وكلمة المرور', 'error'); return; }
  if (password.length < 6) { showToast('كلمة المرور يجب أن تكون ٦ أحرف على الأقل', 'error'); return; }
  const btn = document.getElementById('regBtn');
  btn.textContent = '⏳ جاري إنشاء الشركة...'; btn.disabled = true;
  const d = await api('/auth/register-company', {company_name, full_name: username, username, password, phone});
  btn.textContent = 'إنشاء الشركة'; btn.disabled = false;
  if (d.ok && d.data) {
    closeSheet('regSheet');
    document.getElementById('username').value = username;
    showToast('✅ ' + (d.data.message_ar || 'تم التسجيل'));
    document.getElementById('password').focus();
  } else {
    showToast((d.error && d.error.message_ar) || 'فشل إنشاء الشركة', 'error');
  }
}


// ── COMPANY MANAGEMENT (owner admin) ───────────────────────
function loadAccountsManage() {
  const box = document.getElementById('accountsBox');
  box.innerHTML = '<div class="empty"><div class="big">🏢</div>جارِ التحميل...</div>';
  api('/accounts/manage').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">' + esc((d.error && d.error.message_ar) || 'غير مصرح') + '</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">🏢</div>لا توجد شركات</div>'; return; }
    const pend = items.filter(a => a.pending);
    const approved = items.filter(a => !a.pending && a.id !== 1);
    const usersTxt = a => {
      const n = (a.users || []).length;
      const pendN = (a.users || []).filter(u => u.pending).length;
      return n + ' مستخدم' + (pendN ? ' (' + pendN + ' معلق)' : '');
    };
    box.innerHTML =
      (pend.length
        ? '<div class="section-label">⚠️ بانتظار موافقتك</div><div class="list-card">' + pend.map(a =>
            '<div class="row"><div class="row-main"><div class="row-title">' + esc(a.name) + '</div>' +
            '<div class="row-sub">' + esc(a.phone || '') + ' · ' + usersTxt(a) + '</div></div>' +
            '<div class="row-end"><button class="btn btn-sm" style="color:var(--active);" onclick="approveAccount(' + a.id + ')">✅ موافقة</button></div></div>'
          ).join('') + '</div>'
        : '<div class="card card-pad" style="color:var(--active);font-size:13px;">لا توجد طلبات معلقة ✅</div>') +
      (approved.length
        ? '<div class="section-label">الشركات النشطة</div><div class="list-card">' + approved.map(a =>
            '<div class="row"><div class="row-main"><div class="row-title">' + esc(a.name) + '</div>' +
            '<div class="row-sub">' + (a.status === 'suspended' ? 'موقوفة' : 'نشطة') + ' · ' + usersTxt(a) + '</div></div>' +
            '<div class="row-end"><button class="btn btn-sm" style="color:var(--expired);" onclick="suspendAccount(' + a.id + ')">إيقاف</button></div></div>'
          ).join('') + '</div>'
        : '') +
      '<div class="card card-pad" style="margin-top:14px;font-size:12px;color:var(--muted);">🏠 ' + esc((items.find(a => a.id === 1) || {}).name || 'الحساب الرئيسي') + ' — الحساب الرئيسي</div>';
  });
}
async function approveAccount(id) {
  if (!confirm('موافقة على هذه الشركة؟ سيدخل مستخدموها فوراً.')) return;
  const d = await api('/accounts/' + id + '/status', { pending: false }, 'PUT');
  if (d.ok) { showToast('تمت الموافقة — يمكنهم الدخول الآن ✓'); loadAccountsManage(); }
  else showToast((d.error && d.error.message_ar) || 'فشل', 'error');
}
async function suspendAccount(id) {
  if (!confirm('إيقاف هذه الشركة؟ لن يتمكن مستخدموها من الدخول.')) return;
  const d = await api('/accounts/' + id + '/status', { status: 'suspended' }, 'PUT');
  if (d.ok) { showToast('تم الإيقاف ✓'); loadAccountsManage(); }
  else showToast((d.error && d.error.message_ar) || 'فشل', 'error');
}

// ── LOGOUT (overrides the redirect version in app.js) ──
async function logout() {
  try { await api('/auth/logout', {}); } catch (e) {}
  localStorage.removeItem('alnathim_token');
  localStorage.removeItem('alnathim_user');
  closeSheets();
  stopPolling();
  document.getElementById('appShell').classList.add('hidden');
  document.getElementById('loginView').classList.remove('hidden');
  document.getElementById('loginCard').classList.remove('hidden');
  document.getElementById('waitingCard').classList.add('hidden');
  document.getElementById('username').value = '';
  document.getElementById('password').value = '';
  try { history.replaceState(null, '', location.pathname); } catch (e) {}
}

// ── Android back button / gesture ──────────────────────────
(function () {
  if (!window.Capacitor || !window.Capacitor.Plugins || !window.Capacitor.Plugins.App) return;
  window.Capacitor.Plugins.App.addListener('backButton', () => {
    if (!document.getElementById('loginView').classList.contains('hidden')) {
      window.Capacitor.Plugins.App.exitApp();
      return;
    }
    const openSheetEl = document.querySelector('.sheet-overlay.open');
    if (openSheetEl) { closeSheets(); return; }
    const h = (location.hash || '').replace(/^#/, '');
    if (h && h !== '/view/dashboard') { goBack(); return; }
    window.Capacitor.Plugins.App.exitApp();
  });
})();

// ── Boot ───────────────────────────────────────────────────
document.querySelectorAll('.bottom-nav a[data-nav]').forEach(a => {
  a.addEventListener('click', (e) => { e.preventDefault(); goTo('/view/' + a.dataset.nav); });
});
document.addEventListener('click', (e) => {
  const a = e.target.closest('a');
  if (!a) return;
  const href = a.getAttribute('href') || '';
  if (href.indexOf('customer-detail.html') === 0) {
    e.preventDefault();
    const id = new URLSearchParams(href.split('?')[1]).get('id');
    if (id) goTo('/customer/' + encodeURIComponent(id));
  }
});
window.addEventListener('hashchange', () => applyHash(location.hash));

const savedServer = localStorage.getItem('alnathim_server') || 'https://alnathim.onrender.com';
const serverBaseEl = document.getElementById('serverBase');
if (serverBaseEl) serverBaseEl.value = savedServer;
refreshApiBase();

if (localStorage.getItem('alnathim_token')) {
  enterApp();
} else {
  document.getElementById('loginView').classList.remove('hidden');
}
// ── REMINDERS ──────────────────────────────────────────────
function loadReminders() {
  const box = document.getElementById('remindersBox');
  box.innerHTML = '<div class="empty"><div class="big">⏰</div>جارِ التحميل...</div>';
  api('/reminders').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">✅</div>لا توجد تذكيرات</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(r => {
      const wa = (r.whatsapp_phone || r.phone || '').replace(/[^0-9]/g, '');
      return '<div class="row">' +
        stateDot(r.expired ? 'expired' : 'active') +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(r.name) + '</div>' +
          '<div class="row-sub">' + esc(r.phone || '') + ' · ' + esc(r.package_name || '') +
            ' · ينتهي ' + esc(String(r.renewal_date || '').slice(0, 10)) + '</div>' +
          '<div style="font-size:11px;font-weight:800;color:var(--expired);margin-top:2px;">دين ' + formatDinar(r.total_debt || 0) + '</div>' +
        '</div>' +
        '<div class="row-end">' +
          '<a href="customer-detail.html?id=' + r.customer_id + '" class="btn btn-sm" style="text-decoration:none;">عرض</a>' +
          (wa ? '<a href="https://wa.me/' + wa + '" class="btn btn-sm" style="text-decoration:none;margin-top:6px;color:var(--active);">واتساب</a>' : '') +
        '</div>' +
      '</div>';
    }).join('') + '</div>';
  });
}

// ── EXPENSES ───────────────────────────────────────────────
function loadExpenses() {
  const now = new Date();
  document.getElementById('exMonth').value = String(now.getMonth() + 1);
  document.getElementById('exYear').value = String(now.getFullYear());
  fetchExpenses();
}
function fetchExpenses() {
  const month = document.getElementById('exMonth').value;
  const year = document.getElementById('exYear').value;
  const box = document.getElementById('expensesBox');
  const cacheKey = 'expenses_' + month + '_' + year;
  function render(d) {
    document.getElementById('expTotal').textContent = formatDinar(d.total || 0);
    const items = d.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">💸</div>لا توجد مصاريف لهذا الشهر</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(e =>
      '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(e.category) + (e.subscriber_name ? ' · ' + esc(e.subscriber_name) : '') + '</div>' +
          '<div class="row-sub">' + esc(e.expense_date || '') + (e.description ? ' · ' + esc(e.description) : '') + '</div>' +
        '</div>' +
        '<div class="row-end" style="text-align:left;">' +
          '<div style="font-weight:800;color:var(--expired);">' + formatDinar(e.amount || 0) + '</div>' +
          (IS_ADMIN ? '<div style="display:flex;gap:6px;margin-top:6px;">' +
            '<button class="btn btn-sm" onclick="editExpense(' + e.id + ')">تعديل</button>' +
            '<button class="btn btn-sm" style="color:var(--expired);" onclick="deleteExpense(' + e.id + ')">حذف</button>' +
          '</div>' : '') +
        '</div>' +
      '</div>').join('') + '</div>';
  }
  const cached = cacheGet(cacheKey);
  if (cached) render(cached);
  api('/expenses?month=' + month + '&year=' + year).then(d => {
    if (d.ok) { cacheSet(cacheKey, d.data); render(d.data); }
  });
}
let editingExpenseId = null;
async function saveExpense() {
  const amount = parseInt(document.getElementById('exAmount').value) || 0;
  const category = document.getElementById('exCategory').value.trim();
  if (!category || amount <= 0) { showToast('أدخل التصنيف والمبلغ', 'error'); return; }
  const body = {
    amount: amount,
    category: category,
    expense_date: document.getElementById('exDate').value || '',
    description: document.getElementById('exDesc').value.trim(),
    recipient_name: document.getElementById('exRecipient').value.trim()
  };
  const d = editingExpenseId
    ? await api('/expenses/' + editingExpenseId, body, 'PUT')
    : await api('/expenses', body);
  if (d.ok) { closeSheet('expenseSheet'); editingExpenseId = null; showToast('تم الحفظ ✓'); fetchExpenses(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الإضافة', 'error');
}

// ── TICKETS ────────────────────────────────────────────────
function loadTickets() {
  api('/tickets').then(d => {
    const box = document.getElementById('ticketsBox');
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">🎫</div>لا توجد تذاكر صيانة</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(t => {
      const resolved = String(t.status) === 'resolved';
      return '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(t.customer_name || 'زبون #' + t.customer_id) + '</div>' +
          '<div class="row-sub">' + esc(t.issue_description || '') + '</div>' +
          '<div style="font-size:11px;margin-top:2px;"><span class="badge ' + (resolved ? 'badge-ok' : 'badge-bad') + '">' + (resolved ? 'تم الحل ✓' : 'قيد الانتظار') + '</span> · ' + esc(t.created_at || '') + '</div>' +
        '</div>' +
        '<div class="row-end"><button class="btn btn-sm" onclick="toggleTicket(' + t.id + ')">' + (resolved ? 'إعادة' : 'حل') + '</button></div>' +
      '</div>';
    }).join('') + '</div>';
  });
}
async function toggleTicket(id) {
  const d = await api('/tickets/' + id + '/status', {}, 'PUT');
  if (d.ok) { showToast('تم التحديث ✓'); loadTickets(); }
  else showToast((d.error && d.error.message_ar) || 'فشل التحديث', 'error');
}
function openTicketSheet() {
  const sel = document.getElementById('tkCustomer');
  sel.innerHTML = '<option value="">جارِ التحميل...</option>';
  openSheet('ticketSheet');
  api('/customers').then(d => {
    const items = (d.ok && d.data && d.data.items) || [];
    sel.innerHTML = '<option value="">اختر المشترك</option>' + items.map(c =>
      '<option value="' + c.id + '">' + esc(c.name) + '</option>').join('');
  });
}
async function addTicket() {
  const customerId = document.getElementById('tkCustomer').value;
  const issue = document.getElementById('tkIssue').value.trim();
  if (!customerId || !issue) { showToast('اختر المشترك واكتب المشكلة', 'error'); return; }
  const d = await api('/tickets', { customer_id: parseInt(customerId, 10), issue_description: issue });
  if (d.ok) { closeSheet('ticketSheet'); showToast('تم إنشاء التذكرة ✓'); loadTickets(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الإنشاء', 'error');
}
// ── PACKAGES ───────────────────────────────────────────────
function loadPackages() {
  api('/packages').then(d => {
    const box = document.getElementById('packagesBox');
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">📦</div>لا توجد باقات</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(p =>
      '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(p.name) + '</div>' +
          '<div class="row-sub">' + (p.speed ? esc(p.speed) + ' · ' : '') + formatDinar(p.price || 0) + '</div>' +
        '</div>' +
        '<div class="row-end">' +
          '<button class="btn btn-sm" onclick="editPackage(' + p.id + ')">تعديل</button>' +
          '<button class="btn btn-sm" style="color:var(--expired);margin-top:6px;" onclick="deletePackage(' + p.id + ')">حذف</button>' +
        '</div>' +
      '</div>').join('') + '</div>';
  });
}
let editingPackageId = null;
function openPackageSheet(p) {
  editingPackageId = p ? p.id : null;
  document.getElementById('pkgTitle').textContent = p ? 'تعديل الباقة' : 'إضافة باقة';
  document.getElementById('pkgName').value = p ? (p.name || '') : '';
  document.getElementById('pkgPrice').value = p ? (p.price || '') : '';
  document.getElementById('pkgSpeed').value = p ? (p.speed || '') : '';
  openSheet('packageSheet');
}
function editPackage(id) {
  api('/packages').then(d => {
    const p = ((d.ok && d.data && d.data.items) || []).find(x => x.id === id);
    openPackageSheet(p || { id: id });
  });
}
async function savePackage() {
  const name = document.getElementById('pkgName').value.trim();
  const price = parseInt(document.getElementById('pkgPrice').value) || 0;
  const speed = document.getElementById('pkgSpeed').value.trim();
  if (!name) { showToast('اسم الباقة مطلوب', 'error'); return; }
  const body = { name: name, price: price, speed: speed };
  const d = editingPackageId
    ? await api('/packages/' + editingPackageId, body, 'PUT')
    : await api('/packages', body);
  if (d.ok) { closeSheet('packageSheet'); showToast('تم الحفظ ✓'); loadPackages(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
}
async function deletePackage(id) {
  if (!confirm('حذف هذه الباقة؟')) return;
  const d = await api('/packages/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); loadPackages(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}

// ── CABINETS (FAT) ─────────────────────────────────────────
function loadCabinets() {
  const box = document.getElementById('cabinetsBox');
  box.innerHTML = '<div class="empty"><div class="big">📟</div>جارِ التحميل...</div>';
  api('/cabinets').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    window._cabinets = items;
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">📟</div>لا توجد كابينات — أضف أول كابينة</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(c => {
      const pct = c.port_count ? Math.round((c.used_ports || 0) / c.port_count * 100) : 0;
      return '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(c.fat_number) + '</div>' +
          '<div class="row-sub">' + esc(c.location || '') + (c.region ? ' · ' + esc(c.region) : '') + '</div>' +
          '<div style="font-size:11px;margin-top:4px;color:var(--muted);">' +
            'مشغول ' + toArabicNum(c.used_ports || 0) + ' من ' + toArabicNum(c.port_count || 0) +
            ' <span class="badge ' + (c.free_ports > 0 ? 'badge-ok' : 'badge-bad') + '">' + (c.free_ports > 0 ? (toArabicNum(c.free_ports) + ' شاغر') : 'ممتلئة') + '</span>' +
            '<div class="meter" style="margin-top:4px;"><div style="width:' + pct + '%;"></div></div>' +
          '</div>' +
        '</div>' +
        (IS_ADMIN ? '<div class="row-end"><button class="btn btn-sm" onclick="openCabinetSheet(' + c.id + ')">تعديل</button>' +
          '<button class="btn btn-sm" style="color:var(--expired);margin-top:6px;" onclick="deleteCabinet(' + c.id + ')">حذف</button></div>' : '') +
      '</div>';
    }).join('') + '</div>';
  });
}
let editingCabinetId = null;
function openCabinetSheet(id) {
  editingCabinetId = id || null;
  document.getElementById('cabTitle').textContent = id ? 'تعديل كابينة' : 'إضافة كابينة';
  const c = (id && (window._cabinets || [])).find(x => x.id === id);
  document.getElementById('cabNumber').value = c ? c.fat_number : '';
  document.getElementById('cabPorts').value = c ? c.port_count : 16;
  document.getElementById('cabLocation').value = c ? (c.location || '') : '';
  document.getElementById('cabNotes').value = c ? (c.notes || '') : '';
  openSheet('cabinetSheet');
}
async function saveCabinet() {
  const fat_number = document.getElementById('cabNumber').value.trim();
  const port_count = parseInt(document.getElementById('cabPorts').value) || 16;
  if (!fat_number) { showToast('رقم الكابينة مطلوب', 'error'); return; }
  const body = { fat_number: fat_number, port_count: port_count, location: document.getElementById('cabLocation').value.trim(), notes: document.getElementById('cabNotes').value.trim() };
  const d = editingCabinetId
    ? await api('/cabinets/' + editingCabinetId, body, 'PUT')
    : await api('/cabinets', body);
  if (d.ok) { closeSheet('cabinetSheet'); showToast('تم الحفظ ✓'); loadCabinets(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
}
async function deleteCabinet(id) {
  if (!confirm('حذف هذه الكابينة؟ سيُلغى تعيين منافذ مشتركيها.')) return;
  const d = await api('/cabinets/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); loadCabinets(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}

// ── FAST FAT/PORT ENTRY (admin) ─────────────────────────────
let feAll = [];          // all customers
let feUnassigned = [];   // customers without a port
let feCabs = [];         // all cabinets
let feFilterMode = 'unassigned';
let feRows = [];         // visible rows [{id, fat, port}]
let feIndex = 0;

function loadFatEntry() {
  feIndex = 0;
  Promise.all([api('/customers'), api('/cabinets')]).then(([cd, cabd]) => {
    feCabs = (cabd.ok && cabd.data && cabd.data.items) || [];
    window._cabinets = feCabs;
    const all = (cd.ok && cd.data && cd.data.items) || [];
    feAll = all;
    feUnassigned = all.filter(c => !c.fat_id || !c.port_number);
    feFilter(null, feFilterMode || 'unassigned');
  });
}
function feFilter(btn, mode) {
  feFilterMode = mode;
  if (btn) {
    document.querySelectorAll('#feChips .chip').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }
  feRows = (mode === 'all' ? feAll : feUnassigned).map(c => ({
    id: c.id,
    name: c.name || '',
    fat: c.fat_id || '',
    port: c.port_number || ''
  }));
  feIndex = 0;
  renderFatEntry();
}
function renderFatEntry() {
  const box = document.getElementById('fatEntryBox');
  const prog = document.getElementById('feProgress');
  if (!feRows.length) {
    box.innerHTML = '<div class="empty"><div class="big">🎯</div>كل المشتركين معيّنين — مبروك!</div>';
    prog.textContent = 'مكتمل ✅';
    return;
  }
  prog.textContent = 'باقي ' + toArabicNum(feUnassigned.length) + ' مشترك بدون منفذ · إجمالي ' + toArabicNum(feRows.length);
  box.innerHTML = '<div class="list-card">' + feRows.map((r, i) => {
    const opts = '<option value="">—</option>' + feCabs.map(c =>
      '<option value="' + c.id + '"' + (String(r.fat) === String(c.id) ? ' selected' : '') + '>' + esc(c.fat_number) +
      ' (' + toArabicNum(c.used_ports) + '/' + toArabicNum(c.port_count) + ')</option>').join('');
    return '<div class="row fe-row' + (i === feIndex ? ' fe-active' : '') + '" data-fei="' + i + '">' +
      '<div class="row-main">' +
        '<div class="row-title">' + esc(r.name) + '</div>' +
        '<div class="row-sub" style="display:flex;gap:6px;margin-top:4px;">' +
          '<select class="input fe-fat" style="min-width:0;flex:1;padding:6px;" data-i="' + i + '">' + opts + '</select>' +
          '<input type="number" class="input fe-port" style="width:70px;padding:6px;text-align:center;" min="1" max="64" placeholder="منفذ" value="' + esc(r.port) + '" data-i="' + i + '">' +
        '</div>' +
      '</div>' +
      '<div class="row-end"><button class="btn btn-sm fe-save" data-i="' + i + '" onclick="feSaveRow(' + i + ')">حفظ</button></div>' +
    '</div>';
  }).join('') + '</div>';
}
function feRowData(i) {
  const row = feRows[i];
  if (!row) return { id: null, fat: '', port: '' };
  const sel = document.querySelector('.fe-fat[data-i="' + i + '"]');
  const inp = document.querySelector('.fe-port[data-i="' + i + '"]');
  return { id: row.id, fat: sel ? (sel.value || '') : '', port: inp ? inp.value : '' };
}
async function feSaveRow(i) {
  const r = feRows[i];
  if (!r) return;
  const { id, fat, port } = feRowData(i);
  if (!fat && !port) { showToast('اختر الكابينة أو أزل التعيين أولاً', 'error'); return; }
  if (!fat || !port) { showToast('الكابينة والمنفذ معاً', 'error'); return; }
  const d = await api('/customers/' + id, { fat_id: parseInt(fat, 10), port_number: parseInt(port, 10) }, 'PUT');
  if (d.ok) {
    showToast('تم حفظ ' + r.name + ' ✓');
    const cust = feAll.find(x => x.id === id);
    if (cust) { cust.fat_id = parseInt(fat, 10); cust.port_number = parseInt(port, 10); }
    feUnassigned = feUnassigned.filter(x => x.id !== id);
    if (feFilterMode === 'unassigned') {
      feRows.splice(i, 1);
      if (feIndex >= feRows.length) feIndex = Math.max(0, feRows.length - 1);
    } else {
      feRows[i].fat = fat; feRows[i].port = port;
    }
    renderFatEntry();
  } else {
    showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
  }
}

async function feSaveAll() {
  let ok = 0, errors = 0;
  const snapshot = feRows.map((r, i) => ({ i: i, data: feRowData(i) }));
  for (const { i, data } of snapshot) {
    if (!data.fat && !data.port) { errors++; continue; }
    if (!data.fat || !data.port) { showToast('صف ' + (i + 1) + ': الكابينة والمنفذ معاً', 'error'); errors++; continue; }
    const d = await api('/customers/' + data.id, { fat_id: parseInt(data.fat, 10), port_number: parseInt(data.port, 10) }, 'PUT');
    if (d.ok) ok++; else { errors++; showToast((d.error && d.error.message_ar) || 'فشل في صف ' + (i + 1), 'error'); }
  }
  showToast('تم حفظ ' + ok + ' · أخطاء ' + errors);
  loadFatEntry();
}
function feAutoFill() {
  if (!feCabs.length) { showToast('لا توجد كابينات — أضف كابينة أولاً', 'error'); return; }
  const used = {};
  feCabs.forEach(c => { used[c.id] = c.used_ports || 0; });
  feRows.forEach((r, i) => {
    const pc = r.fat ? feCabs.find(c => c.id === r.fat) : null;
    if (pc) {
      if (!r.port) {
        const taken = new Set();
        feRows.forEach((o, j) => { if (j !== i && String(o.fat) === String(pc.id) && o.port) taken.add(parseInt(o.port, 10)); });
        for (let p = 1; p <= pc.port_count; p++) {
          if (!taken.has(p)) { r.port = p; used[pc.id]++; break; }
        }
      }
    } else {
      for (const c of feCabs) {
        if (used[c.id] >= c.port_count) continue;
        const taken = new Set();
        feRows.forEach((o, j) => { if (j !== i && String(o.fat) === String(c.id) && o.port) taken.add(parseInt(o.port, 10)); });
        for (let p = 1; p <= c.port_count; p++) {
          if (!taken.has(p)) { r.fat = c.id; r.port = p; used[c.id]++; break; }
        }
        break;
      }
    }
  });
  renderFatEntry();
}
// Keyboard navigation: ↑/↓ move rows, Enter saves and advances.
document.addEventListener('keydown', (e) => {
  const feView = document.getElementById('view-fatentry');
  if (!feView || !feView.classList.contains('active') || !feRows.length) return;
  const tag = (e.target.tagName || '').toLowerCase();
  const interactive = tag === 'input' || tag === 'select' || tag === 'textarea';
  if (e.key === 'ArrowDown') {
    if (!interactive) { e.preventDefault(); feIndex = Math.min(feRows.length - 1, feIndex + 1); renderFatEntry(); }
  } else if (e.key === 'ArrowUp') {
    if (!interactive) { e.preventDefault(); feIndex = Math.max(0, feIndex - 1); renderFatEntry(); }
  } else if (e.key === 'Enter') {
    if (interactive) {
      e.preventDefault();
      feSaveRow(feIndex).then(() => {
        if (feRows.length) { feIndex = Math.min(feRows.length - 1, feIndex + 1); renderFatEntry(); }
      });
    }
  }
});
// keep the highlighted row in sync when a control is focused
document.addEventListener('focusin', (e) => {
  const el = e.target.closest && e.target.closest('.fe-row');
  if (!el) return;
  const i = parseInt(el.dataset.fei, 10);
  if (!isNaN(i)) { feIndex = i; renderFatEntry(); }
});

// ── GENERATOR INFO (company profile) ───────────────────────
function loadGenerator() {
  api('/generator-info').then(d => {
    const info = (d.ok && d.data && d.data.generator_info) || {};
    document.getElementById('genOwner').value = info.owner_name || '';
    document.getElementById('genPhone').value = info.phone || '';
    document.getElementById('genAddress').value = info.address || '';
    document.getElementById('genFooter').value = info.footer_note || '';
  });
}
async function saveGenerator() {
  const d = await api('/generator-info', {
    owner_name: document.getElementById('genOwner').value.trim(),
    phone: document.getElementById('genPhone').value.trim(),
    address: document.getElementById('genAddress').value.trim(),
    footer_note: document.getElementById('genFooter').value.trim()
  }, 'PUT');
  if (d.ok) showToast('تم الحفظ ✓');
  else showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
}

// ── TEAM ───────────────────────────────────────────────────
function loadTeam() {
  api('/team').then(d => {
    const box = document.getElementById('teamBox');
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">👥</div>لا يوجد أعضاء</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(u => {
      const active = String(u.status) !== 'suspended';
      return '<div class="row">' +
        stateDot(active ? 'active' : 'suspended') +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(u.full_name || u.username) + '</div>' +
          '<div class="row-sub">@' + esc(u.username) + ' · ' + (u.role === 'admin' ? 'مدير' : 'وكيل') + (u.phone ? ' · ' + esc(u.phone) : '') + '</div>' +
          (u.access_expires ? '<div style="font-size:11px;color:var(--muted);margin-top:2px;">ينتهي: ' + esc(u.access_expires) + '</div>' : '') +
        '</div>' +
        '<div class="row-end" style="text-align:left;">' +
          '<button class="btn btn-sm" onclick="toggleTeam(' + u.id + ')">' + (active ? 'إيقاف' : 'تفعيل') + '</button>' +
          '<div style="display:flex;gap:6px;margin-top:6px;">' +
            '<button class="btn btn-sm" onclick="editTeam(' + u.id + ')">تعديل</button>' +
            '<button class="btn btn-sm" style="color:var(--expired);" onclick="deleteTeam(' + u.id + ')">حذف</button>' +
          '</div>' +
        '</div>' +
      '</div>';
    }).join('') + '</div>';
  });
}
async function toggleTeam(id) {
  const d = await api('/team');
  const u = ((d.ok && d.data && d.data.items) || []).find(x => x.id === id);
  const newStatus = String(u && u.status) === 'suspended' ? 'active' : 'suspended';
  const r = await api('/team/' + id + '/status', { status: newStatus }, 'PUT');
  if (r.ok) { showToast('تم التغيير ✓'); loadTeam(); }
  else showToast((r.error && r.error.message_ar) || 'فشل التغيير', 'error');
}
async function addTeam() {
  const username = document.getElementById('tmUsername').value.trim();
  const password = document.getElementById('tmPassword').value;
  const fullName = document.getElementById('tmName').value.trim();
  const phone = document.getElementById('tmPhone').value.trim();
  const role = document.getElementById('tmRole').value;
  if (!username || password.length < 6) { showToast('اسم مستخدم وكلمة مرور (٦+ أحرف)', 'error'); return; }
  const d = await api('/team', { username: username, password: password, role: role, full_name: fullName, phone: phone });
  if (d.ok) { closeSheet('teamSheet'); showToast('تمت الإضافة ✓'); loadTeam(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الإضافة', 'error');
}
// ── AUDIT ──────────────────────────────────────────────────
function loadAudit() {
  const box = document.getElementById('auditBox');
  box.innerHTML = '<div class="empty"><div class="big">📜</div>جارِ التحميل...</div>';
  api('/audit?limit=100').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">📜</div>لا يوجد سجل</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(a =>
      '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(a.action || '') + '</div>' +
          '<div class="row-sub">' + (a.details ? esc(a.details) + ' · ' : '') + esc(a.created_at || '') + '</div>' +
        '</div>' +
        '<div class="row-end" style="font-size:11px;color:var(--muted);">' + esc(a.username || '') + '</div>' +
      '</div>').join('') + '</div>';
  });
}

// ── BACKUP ─────────────────────────────────────────────────
function loadBackup() {}
function doBackup() {
  const btn = document.getElementById('backupBtn');
  const box = document.getElementById('backupBox');
  btn.disabled = true; btn.textContent = '⏳ جاري الإنشاء...';
  api('/backup', {}).then(d => {
    btn.disabled = false; btn.textContent = 'إنشاء نسخة احتياطية';
    if (d.ok) box.innerHTML = '<div class="card card-pad" style="color:var(--active);font-size:13px;">✅ تم إنشاء النسخة: ' + esc(d.data.backup_path || '') + '</div>';
    else box.innerHTML = '<div class="card card-pad" style="color:var(--expired);font-size:13px;">' + esc((d.error && d.error.message_ar) || 'فشل النسخ الاحتياطي') + '</div>';
  });
}

// ── NETWORK (signal board + links + ping) ──────────────────
function loadNetwork() { showNetworkTab('signal'); }
function showNetworkTab(tab) {
  if (tab === 'links') tab = 'signal';
  document.querySelectorAll('#netTabs .chip').forEach(b => b.classList.toggle('active', b.dataset.ntab === tab));
  document.getElementById('netSignal').classList.toggle('hidden', tab !== 'signal');
  document.getElementById('netPing').classList.toggle('hidden', tab !== 'ping');
  if (tab === 'signal') loadSignalBoard();
}
function signalQuality(dbm) {
  const n = parseFloat(dbm);
  if (isNaN(n)) return 'mid';
  if (n >= -60) return 'ok';
  if (n >= -75) return 'mid';
  return 'bad';
}
function loadSignalBoard() {
  const box = document.getElementById('signalList');
  api('/signal-board').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    document.getElementById('signalUpdated').textContent = d.data.last_update ? 'آخر تحديث: ' + esc(d.data.last_update) : '';
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">📡</div>لا توجد إشارات مسجلة</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(s => {
      const q = signalQuality(s.signal_dbm);
      return '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(s.name || s.ip) + '</div>' +
          '<div class="row-sub" dir="ltr" style="text-align:right;">' + esc(s.ip) +
            (s.ccq !== undefined && s.ccq !== null && s.ccq !== '' ? ' · CCQ ' + esc(s.ccq) + '%' : '') +
            (s.rx_dbm !== undefined && s.rx_dbm !== null && s.rx_dbm !== '' ? ' · RX ' + esc(s.rx_dbm) : '') +
            (s.tx_dbm !== undefined && s.tx_dbm !== null && s.tx_dbm !== '' ? ' · TX ' + esc(s.tx_dbm) : '') +
          '</div>' +
        '</div>' +
        '<div class="row-end"><span class="badge badge-' + q + '">' + esc(s.signal_dbm || '—') + ' dBm</span></div>' +
      '</div>';
    }).join('') + '</div>';
  });
}
function loadNetLinks() {
  const box = document.getElementById('linksList');
  api('/network/links').then(d => {
    if (!d.ok) { box.innerHTML = '<div class="empty">تعذر التحميل</div>'; return; }
    const items = d.data.items || [];
    if (!items.length) { box.innerHTML = '<div class="empty"><div class="big">🌐</div>لا توجد أجهزة شبكة</div>'; return; }
    box.innerHTML = '<div class="list-card">' + items.map(l =>
      '<div class="row">' +
        '<div class="row-main">' +
          '<div class="row-title">' + esc(l.name) + '</div>' +
          '<div class="row-sub" dir="ltr" style="text-align:right;">' + esc(l.ip || '') + ' · ' + esc(l.link_type || '') + (l.location ? ' · ' + esc(l.location) : '') + '</div>' +
        '</div>' +
        '<div class="row-end">' +
          '<button class="btn btn-sm" onclick="editLink(' + l.id + ')">تعديل</button>' +
          '<button class="btn btn-sm" style="color:var(--expired);margin-top:6px;" onclick="deleteLink(' + l.id + ')">حذف</button>' +
        '</div>' +
      '</div>').join('') + '</div>';
  });
}
let editingLinkId = null;
function openLinkSheet(l) {
  editingLinkId = l ? l.id : null;
  document.getElementById('lkTitle').textContent = l ? 'تعديل الجهاز' : 'إضافة جهاز';
  document.getElementById('lkName').value = l ? (l.name || '') : '';
  document.getElementById('lkIp').value = l ? (l.ip || '') : '';
  document.getElementById('lkType').value = l ? (l.link_type || 'MikroTik') : 'MikroTik';
  document.getElementById('lkLocation').value = l ? (l.location || '') : '';
  document.getElementById('lkNotes').value = l ? (l.notes || '') : '';
  document.getElementById('lkUser').value = l ? (l.username || '') : '';
  document.getElementById('lkPass').value = '';
  document.getElementById('lkCommunity').value = l ? (l.community || 'public') : 'public';
  openSheet('linkSheet');
}
function editLink(id) {
  api('/network/links').then(d => {
    const l = ((d.ok && d.data && d.data.items) || []).find(x => x.id === id);
    openLinkSheet(l || { id: id });
  });
}
async function saveLink() {
  const name = document.getElementById('lkName').value.trim();
  if (!name) { showToast('اسم الجهاز مطلوب', 'error'); return; }
  const body = {
    name: name,
    ip: document.getElementById('lkIp').value.trim(),
    link_type: document.getElementById('lkType').value,
    location: document.getElementById('lkLocation').value.trim(),
    notes: document.getElementById('lkNotes').value.trim(),
    username: document.getElementById('lkUser').value.trim(),
    password: document.getElementById('lkPass').value,
    community: document.getElementById('lkCommunity').value.trim() || 'public'
  };
  const d = editingLinkId
    ? await api('/network/links/' + editingLinkId, body, 'PUT')
    : await api('/network/links', body);
  if (d.ok) { closeSheet('linkSheet'); showToast('تم الحفظ ✓'); loadNetLinks(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
}
async function deleteLink(id) {
  if (!confirm('حذف هذا الجهاز؟')) return;
  const d = await api('/network/links/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); loadNetLinks(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}
async function doPing() {
  const host = document.getElementById('pingHost').value.trim();
  if (!host) { showToast('أدخل عنوان IP', 'error'); return; }
  const box = document.getElementById('pingResult');
  box.innerHTML = '<div class="empty">جاري الفحص...</div>';
  const d = await api('/network/ping', { host: host, count: 4 });
  if (d.ok && d.data) {
    const r = d.data;
    const lossColor = (r.loss_percent || 0) > 0 ? 'var(--expired)' : 'var(--active)';
    box.innerHTML = '<div class="card card-pad">' +
      '<div class="metric-grid">' +
        '<div class="metric"><div class="m-label">متوسط</div><div class="m-value">' + (r.avg_ms !== undefined && r.avg_ms !== null ? r.avg_ms + ' ms' : '—') + '</div></div>' +
        '<div class="metric"><div class="m-label">أقل</div><div class="m-value">' + (r.min_ms !== undefined && r.min_ms !== null ? r.min_ms + ' ms' : '—') + '</div></div>' +
        '<div class="metric"><div class="m-label">أعلى</div><div class="m-value">' + (r.max_ms !== undefined && r.max_ms !== null ? r.max_ms + ' ms' : '—') + '</div></div>' +
        '<div class="metric"><div class="m-label">فقدان</div><div class="m-value" style="color:' + lossColor + ';">' + (r.loss_percent || 0) + '%</div></div>' +
      '</div>' +
      (r.error ? '<div style="text-align:center;font-size:12px;color:var(--expired);margin-top:8px;">' + esc(r.error) + '</div>' : '') +
    '</div>';
  } else {
    box.innerHTML = '<div class="card card-pad" style="color:var(--expired);font-weight:700;text-align:center;">' + esc((d.error && d.error.message_ar) || 'فشل الفحص') + '</div>';
  }
}
// ── TOWER (MikroTik / OLT / SNMP) ──────────────────────────
function loadTower() {
  api('/tower-connection').then(d => {
    const s = (d.ok && d.data && d.data.tower_connection) || {};
    document.getElementById('twHost').value = s.mikrotik_host || '';
    document.getElementById('twPort').value = s.mikrotik_port || 8728;
    document.getElementById('twUser').value = s.mikrotik_user || '';
    document.getElementById('twPass').value = '';
    document.getElementById('twOltIp').value = s.olt_ip || '';
    document.getElementById('twOltCommunity').value = s.olt_snmp_community || '';
    document.getElementById('twCommunity').value = s.snmp_community || 'public';
    document.getElementById('twSnmpPort').value = s.snmp_port || 161;
    document.getElementById('twOidRx').value = s.oid_onu_rx || '';
    document.getElementById('twOidTx').value = s.oid_onu_tx || '';
    document.getElementById('twOidUbntSignal').value = s.oid_ubnt_signal || '';
    document.getElementById('twOidUbntCcq').value = s.oid_ubnt_ccq || '';
    document.getElementById('twOidMikrotik').value = s.oid_mikrotik_signal || '';
  });
}
async function saveTower() {
  const d = await api('/tower-connection', {
    mikrotik_host: document.getElementById('twHost').value.trim(),
    mikrotik_port: parseInt(document.getElementById('twPort').value) || 8728,
    mikrotik_user: document.getElementById('twUser').value.trim(),
    mikrotik_password: document.getElementById('twPass').value,
    olt_ip: document.getElementById('twOltIp').value.trim(),
    olt_snmp_community: document.getElementById('twOltCommunity').value.trim(),
    snmp_community: document.getElementById('twCommunity').value.trim() || 'public',
    snmp_port: parseInt(document.getElementById('twSnmpPort').value) || 161,
    oid_onu_rx: document.getElementById('twOidRx').value.trim(),
    oid_onu_tx: document.getElementById('twOidTx').value.trim(),
    oid_ubnt_signal: document.getElementById('twOidUbntSignal').value.trim(),
    oid_ubnt_ccq: document.getElementById('twOidUbntCcq').value.trim(),
    oid_mikrotik_signal: document.getElementById('twOidMikrotik').value.trim()
  }, 'PUT');
  const box = document.getElementById('towerMsg');
  if (d.ok) box.innerHTML = '<div class="card card-pad" style="color:var(--active);font-size:13px;">✅ تم الحفظ</div>';
  else box.innerHTML = '<div class="card card-pad" style="color:var(--expired);font-size:13px;">' + esc((d.error && d.error.message_ar) || 'فشل الحفظ') + '</div>';
}
async function testTower() {
  const box = document.getElementById('towerMsg');
  box.innerHTML = '<div class="empty">جاري اختبار الاتصال...</div>';
  const d = await api('/tower-test', {});
  if (d.ok) box.innerHTML = '<div class="card card-pad" style="color:var(--active);font-size:13px;">✅ ' + esc(d.data && d.data.message) + '</div>';
  else box.innerHTML = '<div class="card card-pad" style="color:var(--expired);font-size:13px;">' + esc((d.error && d.error.message_ar) || 'فشل الاتصال') + '</div>';
}
// ── PACKAGE SELECT HELPERS ────────────────────────────────
function fillPackagesSelect(sel, currentName) {
  sel.innerHTML = '<option value="">جارِ التحميل...</option>';
  api('/packages').then(d => {
    const items = (d.ok && d.data && d.data.items) || [];
    if (!items.length) {
      sel.innerHTML = '<option value="">لا توجد باقات — أضف باقة من قسم الباقات أولاً</option>';
      return;
    }
    let html = '<option value="">اختر الباقة</option>';
    items.forEach(p => {
      const name = p.name || '';
      const price = p.price || 0;
      html += '<option value="' + esc(name) + '" data-price="' + price + '"' + (name === currentName ? ' selected' : '') + '>' + esc(name) + ' — ' + formatDinar(price) + '</option>';
    });
    sel.innerHTML = html;
  });
}
function fillPriceFromPackage() {
  const sel = document.getElementById('cPackage');
  const opt = sel.options[sel.selectedIndex];
  if (opt && opt.dataset.price) document.getElementById('cPrice').value = opt.dataset.price;
}
function openAddSheet() {
  fillPackagesSelect(document.getElementById('cPackage'), '');
  openSheet('addSheet');
}
// ── REPORT SECTION (dedicated) ────────────────────────────
function loadReportSection() {
  const u = getUser();
  if (u) document.getElementById('topLabel').textContent =
    u.role === 'admin' ? 'مدير' : (u.full_name || u.username || '');
  const now = new Date();
  document.getElementById('rMonth').value = String(now.getMonth() + 1);
  document.getElementById('rYear').value = String(now.getFullYear());
  loadReport();
  loadPayments();
}
// ── ADMIN: expense open/edit/delete ────────────────────────
function openExpenseSheet() {
  editingExpenseId = null;
  document.getElementById('exTitle').textContent = 'إضافة مصروف';
  document.getElementById('exCategory').value = '';
  document.getElementById('exAmount').value = '';
  document.getElementById('exDate').value = '';
  document.getElementById('exDesc').value = '';
  document.getElementById('exRecipient').value = '';
  openSheet('expenseSheet');
}
function editExpense(id) {
  const cached = cacheGet('expenses_' + document.getElementById('exMonth').value + '_' + document.getElementById('exYear').value);
  const items = (cached && cached.items) || [];
  const e = items.find(x => x.id === id);
  editingExpenseId = id;
  document.getElementById('exTitle').textContent = 'تعديل مصروف';
  document.getElementById('exCategory').value = (e && e.category) || '';
  document.getElementById('exAmount').value = (e && e.amount) || '';
  document.getElementById('exDate').value = (e && e.expense_date) || '';
  document.getElementById('exDesc').value = (e && e.description) || '';
  document.getElementById('exRecipient').value = (e && e.recipient_name) || '';
  openSheet('expenseSheet');
}
async function deleteExpense(id) {
  if (!confirm('حذف هذا المصروف؟')) return;
  const d = await api('/expenses/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); fetchExpenses(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}

// ── ADMIN: customer edit / delete ─────────────────────────
function openEditCustSheet() {
  if (!cust) return;
  const c = cust;
  document.getElementById('ecName').value = c.name || '';
  document.getElementById('ecPhone').value = c.phone || '';
  document.getElementById('ecPrice').value = c.package_price || '';
  document.getElementById('ecAddress').value = c.address || '';
  document.getElementById('ecRegion').value = c.region || '';
  document.getElementById('ecUsername').value = c.username || '';
  document.getElementById('ecPassword').value = '';
  document.getElementById('ecIp').value = c.ip_address || '';
  document.getElementById('ecDevice').value = c.device_type || '';
  document.getElementById('ecNotes').value = c.notes || '';
  const ecCab = document.getElementById('ecCabinet');
  if (ecCab) ecCab.value = c.cabinet_number || '';
  const ecPort = document.getElementById('ecPort');
  if (ecPort) ecPort.value = c.port_number || '';
  fillPackagesSelect(document.getElementById('ecPkg'), c.package_name || '');
  openSheet('editCustSheet');
}
async function saveCustEdit() {
  let pkgName = document.getElementById('ecPkg').value;
  let pkgPrice = parseInt(document.getElementById('ecPrice').value) || 0;
  if (!pkgName && cust && cust.package_name) pkgName = cust.package_name;
  if (!pkgPrice && cust && cust.package_price) pkgPrice = cust.package_price;
  const d = await api('/customers/' + currentCustomerId, {
    name: document.getElementById('ecName').value.trim(),
    phone: document.getElementById('ecPhone').value.trim(),
    package_name: pkgName,
    package_price: pkgPrice,
    address: document.getElementById('ecAddress').value.trim(),
    region: document.getElementById('ecRegion').value.trim(),
    username: document.getElementById('ecUsername').value.trim(),
    password: document.getElementById('ecPassword').value,
    ip_address: document.getElementById('ecIp').value.trim(),
    device_type: document.getElementById('ecDevice').value.trim(),
    notes: document.getElementById('ecNotes').value.trim(),
    cabinet_number: document.getElementById('ecCabinet') ? document.getElementById('ecCabinet').value.trim() : '',
    port_number: document.getElementById('ecPort') ? (document.getElementById('ecPort').value || null) : null
  }, 'PUT');
  if (d.ok) { closeSheet('editCustSheet'); showToast('تم الحفظ ✓'); loadCustomer(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحفظ', 'error');
}
async function deleteCustomer() {
  if (!confirm('حذف هذا المشترك نهائياً؟ لا يمكن التراجع.')) return;
  const d = await api('/customers/' + currentCustomerId, null, 'DELETE');
  if (d.ok) {
    cacheClear('customers'); cacheClear('debts'); cacheClear('dashboard');
    showToast('تم حذف المشترك نهائياً');
    goBack();
    loadCustomers();
  }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}

// ── ADMIN: payment edit / delete ──────────────────────────
let editingPaymentId = null;
function editPayment(id) {
  const cached = cacheGet('payments');
  const items = (cached && cached.items) || [];
  const p = items.find(x => x.id === id);
  editingPaymentId = id;
  document.getElementById('peAmount').value = (p && p.amount) || '';
  document.getElementById('peDate').value = (p && p.payment_date) || '';
  document.getElementById('peMethod').value = (p && p.payment_method) || 'نقدي';
  document.getElementById('peNotes').value = (p && p.notes) || '';
  openSheet('payEditSheet');
}
async function savePayment() {
  const amount = parseInt(document.getElementById('peAmount').value) || 0;
  if (amount <= 0) { showToast('مبلغ غير صالح', 'error'); return; }
  const d = await api('/payments/' + editingPaymentId, {
    amount: amount,
    payment_date: document.getElementById('peDate').value || '',
    payment_method: document.getElementById('peMethod').value,
    notes: document.getElementById('peNotes').value.trim()
  }, 'PUT');
  if (d.ok) { closeSheet('payEditSheet'); showToast('تم التعديل ✓'); loadPayments(); }
  else showToast((d.error && d.error.message_ar) || 'فشل التعديل', 'error');
}
async function deletePayment(id) {
  if (!confirm('حذف هذه الدفعة؟')) return;
  const d = await api('/payments/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); loadPayments(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}
// ── ADMIN: team edit / delete ─────────────────────────────
let editingTeamId = null;
function openTeamSheet(u) {
  editingTeamId = u ? u.id : null;
  document.getElementById('teamTitle').textContent = u ? 'تعديل مستخدم' : 'إضافة مستخدم';
  document.getElementById('tmName').value = u ? (u.full_name || '') : '';
  document.getElementById('tmUsername').value = u ? (u.username || '') : '';
  document.getElementById('tmPassword').value = '';
  document.getElementById('tmPhone').value = u ? (u.phone || '') : '';
  document.getElementById('tmRole').value = u ? (u.role || 'agent') : 'agent';
  openSheet('teamSheet');
}
async function saveTeam() {
  const fullName = document.getElementById('tmName').value.trim();
  const phone = document.getElementById('tmPhone').value.trim();
  const role = document.getElementById('tmRole').value;
  const password = document.getElementById('tmPassword').value;
  if (editingTeamId) {
    const body = { full_name: fullName, phone: phone, role: role };
    if (password) body.password = password;
    const d = await api('/team/' + editingTeamId, body, 'PUT');
    if (d.ok) { closeSheet('teamSheet'); editingTeamId = null; showToast('تم التعديل ✓'); loadTeam(); }
    else showToast((d.error && d.error.message_ar) || 'فشل التعديل', 'error');
    return;
  }
  const username = document.getElementById('tmUsername').value.trim();
  if (!username || password.length < 6) { showToast('اسم مستخدم وكلمة مرور (٦+ أحرف)', 'error'); return; }
  const d = await api('/team', { username: username, password: password, role: role, full_name: fullName, phone: phone });
  if (d.ok) { closeSheet('teamSheet'); showToast('تمت الإضافة ✓'); loadTeam(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الإضافة', 'error');
}
function editTeam(id) {
  api('/team').then(d => {
    const u = ((d.ok && d.data && d.data.items) || []).find(x => x.id === id);
    openTeamSheet(u || { id: id });
  });
}
async function deleteTeam(id) {
  if (!confirm('حذف هذا المستخدم؟')) return;
  const d = await api('/team/' + id, null, 'DELETE');
  if (d.ok) { showToast('تم الحذف ✓'); loadTeam(); }
  else showToast((d.error && d.error.message_ar) || 'فشل الحذف', 'error');
}