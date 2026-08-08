/* Al-Nathim premium ISP dashboard SPA — vanilla JS, no frameworks. */
(() => {
  "use strict";

  // ── Config ─────────────────────────────────────────────
  const API_BASE = location.protocol === "file:"
    ? "http://localhost:8000"
    : "";
  const REFRESH_MS = 60000;
  const SIGNAL_BAD_DBM = -27;
  const PAY_DAYS = 30;

  // ── State ──────────────────────────────────────────────
  let users = [];
  let finance = null;
  let current = null; // user shown in the modal
  let activeTab = "dashboard";
  let editingId = null; // null = add mode, number = edit mode
  let searchQuery = "";
  let filterStatus = "all";
  let authToken = sessionStorage.getItem("alnathim_token") || null;

  // ── DOM refs ───────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const userList = $("usersTableBody");
  const debtorList = $("debtorsTableBody");
  const subtitle = $("dashSubtitle");
  const errorBox = $("errorBox");
  const backdrop = $("sheetBackdrop");
  const payBtn = $("payBtn");
  const payError = $("payError");
  const toast = $("toast");
  const historyBody = $("historyTableBody");
  const editBackdrop = $("editBackdrop");
  const editError = $("editError");

  // ── Helpers ────────────────────────────────────────────
  const fmtMoney = (n) => Number(n || 0).toLocaleString("en-US") + " د.ع";

  // HTML entity strings built by concatenation (no literal entity sequences).
  const ESC_MAP = {
    "&": "&" + "amp;",
    "<": "&" + "lt;",
    ">": "&" + "gt;",
    '"': "&" + "quot;",
    "'": "&" + "#39;"
  };
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ESC_MAP[c]);

  const showToast = (msg) => {
    toast.textContent = msg;
    toast.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => toast.classList.remove("show"), 2200);
  };

  async function api(path, options) {
    const opts = options || {};
    opts.headers = Object.assign({}, opts.headers);
    if (authToken) opts.headers["Authorization"] = "Bearer " + authToken;

    const res = await fetchWithRetry(API_BASE + path, opts);
    if (res.status === 401) {
      logout();
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      let detail = "";
      try {
        detail = (await res.json()).detail?.message || "";
      } catch (_) { /* ignore */ }
      const err = new Error(detail || "HTTP " + res.status);
      err.status = res.status;
      throw err;
    }
    return res.json();
  }

  // ── Blood-style speed layer (same feel as the Android APK) ──
  // Fetch with automatic retry — absorbs Render free-tier cold-start
  // blips so the first request never surfaces an error to the user.
  async function fetchWithRetry(url, opts, attempts, delayMs) {
    attempts = attempts || 3;
    delayMs = delayMs || 2000;
    let lastErr;
    for (let i = 0; i < attempts; i++) {
      try {
        return await fetch(url, opts);
      } catch (e) {
        lastErr = e;
        if (i < attempts - 1) await new Promise((r) => setTimeout(r, delayMs));
      }
    }
    throw lastErr;
  }

  // localStorage cache — pages paint instantly from the last good data,
  // then refresh silently in the background (no visible loaders).
  function cacheGet(key) {
    try {
      const raw = localStorage.getItem("alnathim_cache_" + key);
      if (!raw) return null;
      return JSON.parse(raw).data;
    } catch (_) { return null; }
  }
  function cacheSet(key, data) {
    try {
      localStorage.setItem("alnathim_cache_" + key, JSON.stringify({ data: data, ts: Date.now() }));
    } catch (_) { /* storage full — ignore */ }
  }

  // Keep the Render server warm so navigation never hits a 30-60s cold start.
  function startKeepAlive() {
    if (startKeepAlive._t) return;
    startKeepAlive._t = setInterval(() => {
      if (!authToken) return;
      fetchWithRetry(API_BASE + "/api/users", {
        headers: { "Authorization": "Bearer " + authToken }
      })
        .then((r) => { if (r.status === 401) logout(); })
        .catch(() => {});
    }, 600000);
  }

  // ── Auth (Phase 8) ────────────────────────────────────
  function showLogin() {
    $("loginView").classList.remove("hidden");
    $("appShell").classList.add("hidden");
    document.body.style.overflow = "";
  }

  function showApp() {
    $("loginView").classList.add("hidden");
    $("appShell").classList.remove("hidden");
  }

  function logout() {
    authToken = null;
    sessionStorage.removeItem("alnathim_token");
    closeSheet();
    closeEditModal();
    showLogin();
  }

  async function doLogin() {
    const password = $("loginPassword").value;
    $("loginError").classList.add("hidden");
    $("loginBtn").disabled = true;
    $("loginBtn").textContent = "جاري الدخول…";
    try {
      const res = await api("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password })
      });
      authToken = res.token;
      sessionStorage.setItem("alnathim_token", res.token);
      $("loginPassword").value = "";
      showApp();
      switchTab("dashboard");
      // Blood-style speed: seed the debts cache + keep the server warm.
      loadFinance();
      startKeepAlive();
    } catch (e) {
      $("loginError").textContent = (e && e.message && e.message !== "unauthorized")
        ? e.message
        : "كلمة المرور غير صحيحة";
      $("loginError").classList.remove("hidden");
    } finally {
      $("loginBtn").disabled = false;
      $("loginBtn").textContent = "تسجيل الدخول";
    }
  }

  const dotColorFor = (u) =>
    u.is_online ? "bg-emerald-500" : u.status === "expired" ? "bg-rose-500" : "bg-slate-300";

  // ── Tab routing ────────────────────────────────────────
  function switchTab(tab) {
    activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach((b) => {
      const active = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(active));
      b.className = active
        ? "tab-btn active flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition"
        : "tab-btn flex-1 rounded-lg px-3 py-2 text-sm font-semibold transition";
    });
    ["dashboard", "tower", "debts"].forEach((v) => {
      $("view-" + v).classList.toggle("hidden", v !== tab);
    });
    if (tab === "dashboard") loadDashboard();
    if (tab === "debts") loadFinance();
    if (tab === "tower") loadTowerConfig();
  }

  // ── Fetch users + dashboard ────────────────────────────
  async function fetchUsers() {
    const data = await api("/api/users");
    return Array.isArray(data) ? data : [];
  }

  async function loadDashboard() {
    errorBox.classList.add("hidden");
    // Instant paint from cache — Blood-style, no visible loader.
    const cached = cacheGet("dashboard");
    if (cached) {
      users = cached.users || [];
      finance = cached.finance || null;
      renderDashboard();
      if (finance) $("statRevenue").textContent = fmtMoney(finance.total_revenue);
    }
    try {
      users = await fetchUsers();
      renderDashboard();
      // Refresh finance stats lightly for the revenue card.
      try {
        finance = await api("/api/finance");
        $("statRevenue").textContent = fmtMoney(finance.total_revenue);
        cacheSet("dashboard", { users: users, finance: finance });
      } catch (_) {
        cacheSet("dashboard", { users: users, finance: finance || null });
      }
    } catch (e) {
      if (!cached) showError();
    }
  }

  function renderDashboard() {
    userList.innerHTML = "";
    const online = users.filter((u) => u.is_online).length;
    const expired = users.filter((u) => u.status === "expired").length;
    $("statTotal").textContent = users.length;
    $("statOnline").textContent = online;
    $("statExpired").textContent = expired;

    // Real-time filter by name/username and status.
    const q = searchQuery.trim().toLowerCase();
    const filtered = users.filter((u) => {
      if (filterStatus !== "all" && u.status !== filterStatus) return false;
      if (!q) return true;
      const name = (u.name || "").toLowerCase();
      const username = (u.mikrotik_username || "").toLowerCase();
      return name.includes(q) || username.includes(q);
    });

    subtitle.textContent = filtered.length + " / " + users.length + " مشترك";

    if (!filtered.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="5" class="px-4 py-8 text-center text-sm text-slate-400">' +
        (users.length ? "لا توجد نتائج مطابقة" : "لا يوجد مشتركون بعد") + "</td>";
      userList.appendChild(tr);
      return;
    }

    filtered.forEach((u) => {
      const tr = document.createElement("tr");
      tr.className = "border-t border-slate-100 transition hover:bg-slate-50 cursor-pointer";
      tr.innerHTML =
        '<td class="px-4 py-3"><div class="flex items-center gap-2">' +
        '<span class="h-2.5 w-2.5 rounded-full ' + dotColorFor(u) + '"></span>' +
        '<span class="font-medium">' + esc(u.name) + "</span></div></td>" +
        '<td class="px-4 py-3"><span class="rounded-full px-2.5 py-0.5 text-xs font-semibold ' +
        (u.status === "active" ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-600") +
        '">' + (u.status === "active" ? "نشط" : "منتهي") + "</span></td>" +
        '<td class="px-4 py-3 font-semibold text-slate-700">' + fmtMoney(u.balance) + "</td>" +
        '<td class="px-4 py-3 text-slate-500">' + esc(u.expiry_date) + "</td>" +
        '<td class="px-4 py-3 text-slate-500" dir="ltr">' + (u.is_online ? esc(u.ip_address) : "—") + "</td>";
      tr.addEventListener("click", () => openSheet(u));
      userList.appendChild(tr);
    });
  }

  // ── Receipt printing (80mm thermal) ────────────────────
  function printReceipt(data) {
    $("prName").textContent = data.name || "";
    $("prAmount").textContent = data.amount || "";
    $("prDate").textContent = data.date || "";
    $("prExpiry").textContent = data.expiry || "";
    window.print();
  }

  // ── CSV export download (auth-aware) ──────────────────
  async function exportCsv() {
    try {
      const res = await fetchWithRetry(API_BASE + "/api/finance/export", {
        headers: { "Authorization": authToken ? "Bearer " + authToken : "" }
      });
      if (res.status === 401) {
        logout();
        return;
      }
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "subscribers_export.csv";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (_) {
      showToast("فشل تصدير البيانات");
    }
  }

  // ── Finance / debts ────────────────────────────────────
  async function loadFinance() {
    errorBox.classList.add("hidden");
    // Instant paint from cache, then silent background refresh.
    const cached = cacheGet("debts");
    if (cached) {
      finance = cached;
      renderFinance();
    }
    try {
      finance = await api("/api/finance");
      renderFinance();
      cacheSet("debts", finance);
    } catch (e) {
      if (!cached) showError();
    }
  }

  function renderFinance() {
    $("debtTotalUnpaid").textContent = fmtMoney(finance.total_unpaid);
    $("debtTotalRevenue").textContent = fmtMoney(finance.total_revenue);
    $("debtCount").textContent = finance.debtors.length;
    $("debtPaymentCount").textContent = "عدد المدفوعات المسجلة: " + finance.payment_count;

    debtorList.innerHTML = "";
    if (!finance.debtors.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="3" class="px-4 py-8 text-center text-sm text-slate-400">لا توجد ديون حالياً 🎉</td>';
      debtorList.appendChild(tr);
      return;
    }
    finance.debtors.forEach((d) => {
      const tr = document.createElement("tr");
      tr.className = "border-t border-slate-100";
      tr.innerHTML =
        '<td class="px-4 py-3 font-medium">' + esc(d.name) + "</td>" +
        '<td class="px-4 py-3 font-semibold text-rose-600">' + fmtMoney(d.amount) + "</td>" +
        '<td class="px-4 py-3 text-slate-500">' + esc(d.expiry_date) + "</td>";
      debtorList.appendChild(tr);
    });
  }

  // ── Tower connection ───────────────────────────────────
  async function loadTowerConfig() {
    try {
      const cfg = await api("/api/tower-config");
      $("mkHost").value = cfg.mikrotik_host || "";
      $("mkUser").value = cfg.mikrotik_user || "";
      $("mkPassword").value = "";
      $("mkPassword").placeholder = cfg.mikrotik_password_set ? "******** (محفوظ)" : "••••••••";
      $("mkPort").value = cfg.mikrotik_port || 8728;
      $("oltCommunity").value = cfg.olt_community || "";
      $("oltPort").value = cfg.olt_port || 161;
    } catch (_) { /* gateway handles errors */ }
  }

  async function saveTower() {
    $("towerMsg").classList.add("hidden");
    const body = {
      mikrotik_host: $("mkHost").value.trim(),
      mikrotik_user: $("mkUser").value.trim(),
      mikrotik_password: $("mkPassword").value,
      mikrotik_port: parseInt($("mkPort").value, 10) || 8728,
      olt_community: $("oltCommunity").value.trim(),
      olt_port: parseInt($("oltPort").value, 10) || 161
    };
    $("towerSaveBtn").disabled = true;
    $("towerSaveBtn").textContent = "جاري الحفظ…";
    try {
      await api("/api/tower-config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      showToast("تم حفظ الإعدادات بنجاح");
      $("mkPassword").value = "";
      $("mkPassword").placeholder = "******** (محفوظ)";
    } catch (e) {
      showMsg(e.message || "فشل الحفظ", true);
    } finally {
      $("towerSaveBtn").disabled = false;
      $("towerSaveBtn").textContent = "حفظ الإعدادات";
    }
  }

  async function testTower() {
    $("towerMsg").classList.add("hidden");
    const body = {
      mikrotik_host: $("mkHost").value.trim(),
      mikrotik_user: $("mkUser").value.trim(),
      mikrotik_password: $("mkPassword").value,
      mikrotik_port: parseInt($("mkPort").value, 10) || 8728
    };
    $("towerTestBtn").disabled = true;
    $("towerTestBtn").textContent = "جارٍ الاختبار…";
    try {
      const res = await api("/api/tower-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      showMsg(res.message, !res.ok);
    } catch (e) {
      showMsg(e.message || "فشل الاختبار", true);
    } finally {
      $("towerTestBtn").disabled = false;
      $("towerTestBtn").textContent = "اختبار الاتصال";
    }
  }

  function showMsg(text, isError) {
    const el = $("towerMsg");
    el.textContent = text;
    el.className = isError
      ? "rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-600 ring-1 ring-rose-200"
      : "rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700 ring-1 ring-emerald-200";
  }

  // ── Modal / drawer ─────────────────────────────────────
  function openSheet(u) {
    current = u;
    payError.classList.add("hidden");
    payBtn.disabled = false;
    payBtn.textContent = "دفع وتجديد";

    $("sheetName").textContent = u.name || u.mikrotik_username || "";
    $("sheetUsername").textContent = u.mikrotik_username || "";
    $("sheetStatusDot").className = "h-2.5 w-2.5 rounded-full " + dotColorFor(u);
    $("sheetIp").textContent = u.ip_address || "—";
    $("sheetExpiry").textContent = u.expiry_date || "—";

    const pill = $("signalPill");
    const detail = $("signalDetail");
    pill.className = "rounded-full px-3 py-1 text-sm font-semibold";
    pill.textContent = "…";
    detail.textContent = "جاري قراءة الإشارة…";

    backdrop.classList.remove("hidden");
    document.body.style.overflow = "hidden";

    if (u.ip_address) {
      loadSignal(u.ip_address, pill, detail);
    } else {
      pill.className += " bg-slate-200 text-slate-500";
      pill.textContent = "لا توجد إشارة";
      detail.textContent = "المشترك غير متصل الآن";
    }

    loadHistory(u);
  }

  // ── Payment history ────────────────────────────────────
  async function loadHistory(u) {
    historyBody.innerHTML = "";
    if (!u.id) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="3" class="py-2 text-center text-slate-400">لا توجد دفعات بعد</td>';
      historyBody.appendChild(tr);
      return;
    }
    const trLoading = document.createElement("tr");
    trLoading.innerHTML = '<td colspan="3" class="py-2 text-center text-slate-400">جاري التحميل…</td>';
    historyBody.appendChild(trLoading);
    try {
      const payments = await api("/api/users/" + u.id + "/history");
      historyBody.innerHTML = "";
      if (!payments || !payments.length) {
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="3" class="py-2 text-center text-slate-400">لا توجد دفعات بعد</td>';
        historyBody.appendChild(tr);
        return;
      }
      payments.forEach((p) => {
        const tr = document.createElement("tr");
        tr.className = "border-t border-slate-100";
        tr.innerHTML =
          '<td class="py-1.5 font-semibold text-emerald-700">' + fmtMoney(p.amount) + "</td>" +
          '<td class="py-1.5 text-slate-500">' + esc(p.payment_date) + "</td>" +
          '<td class="py-1.5 text-left">' +
          '<button class="print-history-btn rounded p-1 text-slate-400 transition hover:text-emerald-600" ' +
          'title="طباعة وصل" aria-label="طباعة وصل">' +
          '<svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" d="M6.72 13.829c-.24.03-.48.062-.72.096m.72-.096a42.415 42.415 0 0 1 10.56 0m-10.56 0L6.34 18m10.94-4.171c.24.03.48.062.72.096m-.72-.096L17.66 18m0 0l.229 2.523a1.125 1.125 0 0 1-1.12 1.227H7.231c-.662 0-1.18-.568-1.12-1.227L6.34 18m11.318 0h1.091A2.25 2.25 0 0 0 21 15.75V9.456c0-1.081-.768-2.015-1.837-2.175a48.055 48.055 0 0 0-1.913-.247M6.34 18H5.25A2.25 2.25 0 0 1 3 15.75V9.456c0-1.081.768-2.015 1.837-2.175a48.041 48.041 0 0 1 1.913-.247m10.5 0a48.536 48.536 0 0 0-10.5 0m10.5 0V3.375c0-.621-.504-1.125-1.125-1.125h-8.25c-.621 0-1.125.504-1.125 1.125v3.659M18 10.5h.008v.008H18v-.008Z" />' +
          "</svg></button></td>";
        tr.querySelector(".print-history-btn").addEventListener("click", (e) => {
          e.stopPropagation();
          printReceipt({
            name: u.name || u.mikrotik_username || "",
            amount: fmtMoney(p.amount),
            date: p.payment_date,
            expiry: u.expiry_date || ""
          });
        });
        historyBody.appendChild(tr);
      });
    } catch (_) {
      historyBody.innerHTML = "";
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="3" class="py-2 text-center text-slate-400">تعذّر تحميل الدفعات</td>';
      historyBody.appendChild(tr);
    }
  }

  // ── Add / Edit subscriber modal ────────────────────────
  function openEditModal(u) {
    editingId = u && u.id ? u.id : null;
    editError.classList.add("hidden");
    $("editTitle").textContent = editingId ? "تعديل المشترك" : "إضافة مشترك جديد";
    $("editName").value = u && u.name ? u.name : "";
    $("editUsername").value = u && u.mikrotik_username ? u.mikrotik_username : "";
    $("editFee").value = u && u.balance ? u.balance : "";
    $("editExpiry").value = u && u.expiry_date ? u.expiry_date : "";
    $("editDeleteBtn").classList.toggle("hidden", !editingId);
    $("editSaveBtn").textContent = editingId ? "حفظ التعديلات" : "إضافة المشترك";
    editBackdrop.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }

  function closeEditModal() {
    editBackdrop.classList.add("hidden");
    document.body.style.overflow = "";
    editingId = null;
  }

  async function saveUser() {
    editError.classList.add("hidden");
    const body = {
      name: $("editName").value.trim(),
      mikrotik_username: $("editUsername").value.trim(),
      monthly_fee: parseFloat($("editFee").value),
      expiry_date: $("editExpiry").value.trim()
    };
    if (!body.name || !body.mikrotik_username || !body.expiry_date || !(body.monthly_fee > 0)) {
      editError.textContent = "الرجاء ملء جميع الحقول بشكل صحيح";
      editError.classList.remove("hidden");
      return;
    }
    $("editSaveBtn").disabled = true;
    $("editSaveBtn").textContent = "جاري الحفظ…";
    try {
      if (editingId) {
        await api("/api/users/" + editingId, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
        showToast("تم تحديث المشترك بنجاح");
      } else {
        await api("/api/users", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body)
        });
        showToast("تمت إضافة المشترك بنجاح");
      }
      closeEditModal();
      await loadDashboard();
    } catch (e) {
      editError.textContent = e.message || "فشل الحفظ";
      editError.classList.remove("hidden");
    } finally {
      $("editSaveBtn").disabled = false;
      $("editSaveBtn").textContent = editingId ? "حفظ التعديلات" : "إضافة المشترك";
    }
  }

  async function deleteUser() {
    if (!editingId) return;
    const confirmMsg = "هل أنت متأكد من حذف المشترك؟ لا يمكن التراجع.";
    if (!window.confirm(confirmMsg)) return;

    $("editDeleteBtn").disabled = true;
    $("editDeleteBtn").textContent = "جاري الحذف…";
    try {
      await api("/api/users/" + editingId, { method: "DELETE" });
      closeEditModal();
      await loadDashboard();
      showToast("تم حذف المشترك");
    } catch (e) {
      $("editDeleteBtn").disabled = false;
      $("editDeleteBtn").textContent = "حذف المشترك";
      editError.textContent = e.message || "فشل الحذف";
      editError.classList.remove("hidden");
    }
  }

  function closeSheet() {
    backdrop.classList.add("hidden");
    document.body.style.overflow = "";
    current = null;
  }

  async function loadSignal(ip, pill, detail) {
    try {
      const community = ($("oltCommunity") && $("oltCommunity").value.trim())
        ? $("oltCommunity").value.trim()
        : "public";
      const data = await api("/api/signal/" + encodeURIComponent(ip) + "?community=" + encodeURIComponent(community));
      if (data.status !== "good") {
        pill.className += " bg-slate-200 text-slate-500";
        pill.textContent = "لا توجد إشارة";
        detail.textContent = "";
        return;
      }
      const raw = data.rx_dbm != null ? data.rx_dbm : data.signal_dbm;
      const value = raw != null ? parseFloat(raw) : NaN;
      const label = data.rx_dbm != null ? data.rx_dbm : data.signal_dbm;
      if (Number.isFinite(value)) {
        const bad = value < SIGNAL_BAD_DBM;
        pill.className += bad ? " bg-rose-100 text-rose-600" : " bg-emerald-100 text-emerald-700";
        pill.textContent = label + " dBm";
      } else {
        pill.className += " bg-slate-200 text-slate-500";
        pill.textContent = "غير متاح";
      }
      const parts = [];
      if (data.rx_dbm != null) parts.push("RX: " + data.rx_dbm + " dBm");
      if (data.tx_dbm != null) parts.push("TX: " + data.tx_dbm + " dBm");
      if (data.signal_dbm != null) parts.push("الإشارة: " + data.signal_dbm + " dBm");
      if (data.ccq != null) parts.push("CCQ: " + data.ccq + "%");
      detail.textContent = parts.join(" · ");
    } catch (_) {
      pill.className += " bg-slate-200 text-slate-500";
      pill.textContent = "غير متاح";
      detail.textContent = "";
    }
  }

  // ── Pay flow ───────────────────────────────────────────
  async function pay() {
    if (!current) return;
    payError.classList.add("hidden");
    payBtn.disabled = true;
    payBtn.innerHTML =
      '<svg class="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">' +
      '<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>' +
      '<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path></svg> جاري الدفع…';

    const paidName = current.name || current.mikrotik_username || "";
    const body = {
      mikrotik_username: current.mikrotik_username,
      amount: Number(current.balance) || 0,
      days_to_add: PAY_DAYS
    };

    try {
      const res = await api("/api/pay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      const payment = res.payment || {};
      closeSheet();
      await loadDashboard();
      showToast("تم الدفع بنجاح");
      // Auto-print the thermal receipt after a successful payment.
      printReceipt({
        name: paidName,
        amount: fmtMoney(payment.payment_amount || body.amount),
        date: payment.payment_date || "",
        expiry: payment.expiry_date || ""
      });
    } catch (e) {
      payError.textContent = e.message || "فشل الدفع";
      payError.classList.remove("hidden");
      payBtn.disabled = false;
      payBtn.textContent = "دفع وتجديد";
    }
  }

  // ── Error helper ───────────────────────────────────────
  function showError() {
    $("errorMsg").textContent =
      "تعذّر الاتصال بالخادم.\nتأكد من تشغيل الخادم على الموقع:\n" +
      API_BASE + " (مثال: شغّل start_all.bat)";
    errorBox.classList.remove("hidden");
  }

  // ── Events ─────────────────────────────────────────────
  $("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    doLogin();
  });
  $("logoutBtn").addEventListener("click", logout);
  document.querySelectorAll(".tab-btn").forEach((b) =>
    b.addEventListener("click", () => switchTab(b.dataset.tab))
  );
  $("refreshBtn").addEventListener("click", () => {
    if (activeTab === "dashboard") loadDashboard();
    if (activeTab === "debts") loadFinance();
    if (activeTab === "tower") loadTowerConfig();
  });
  $("retryBtn").addEventListener("click", () => switchTab(activeTab));
  $("sheetClose").addEventListener("click", closeSheet);
  backdrop.addEventListener("click", (e) => {
    if (e.target === backdrop) closeSheet();
  });
  payBtn.addEventListener("click", pay);
  $("towerSaveBtn").addEventListener("click", saveTower);
  $("towerTestBtn").addEventListener("click", testTower);
  $("addUserBtn").addEventListener("click", () => openEditModal(null));
  $("editUserBtn").addEventListener("click", () => {
    if (current) openEditModal(current);
  });
  $("editCloseBtn").addEventListener("click", closeEditModal);
  editBackdrop.addEventListener("click", (e) => {
    if (e.target === editBackdrop) closeEditModal();
  });
  $("editSaveBtn").addEventListener("click", saveUser);
  $("editDeleteBtn").addEventListener("click", deleteUser);
  $("exportBtn").addEventListener("click", exportCsv);
  $("searchInput").addEventListener("input", (e) => {
    searchQuery = e.target.value;
    renderDashboard();
  });
  document.querySelectorAll(".filter-btn").forEach((b) =>
    b.addEventListener("click", () => {
      filterStatus = b.dataset.filter;
      document.querySelectorAll(".filter-btn").forEach((x) =>
        x.classList.toggle("active", x === b)
      );
      renderDashboard();
    })
  );

  // ── Boot ───────────────────────────────────────────────
  if (authToken) {
    showApp();
    switchTab("dashboard");
    // Blood-style speed: seed the debts cache + keep the server warm.
    loadFinance();
    startKeepAlive();
  } else {
    showLogin();
  }
  setInterval(() => {
    if (!authToken) return;
    if (activeTab === "dashboard") loadDashboard();
    if (activeTab === "debts") loadFinance();
  }, REFRESH_MS);

  // ── Service worker (safe; skipped on file://) ──────────
  if ("serviceWorker" in navigator && location.protocol !== "file:") {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  }
})();