// Runtime simulation: loads js/app.js + every page's inline script with a
// minimal DOM mock. Asserts (a) every page LOADS without throwing, and
// (b) the index.html login button performs the full flow.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const appJs = fs.readFileSync(path.join(__dirname, 'js', 'app.js'), 'utf8');
const pages = fs.readdirSync(__dirname).filter(f => f.endsWith('.html'));

function makeEl(id) {
  return {
    id, value: '', textContent: '', innerHTML: '',
    style: {},
    disabled: false,
    classList: { _set: new Set(), add(...c) { c.forEach(x => this._set.add(x)); }, remove(...c) { c.forEach(x => this._set.delete(x)); }, toggle(c) { this._set.has(c) ? this._set.delete(c) : this._set.add(c); }, contains(c) { return this._set.has(c); } },
    focus() {}, appendChild() {}, addEventListener() {}, remove() {}
  };
}
function makeSandbox(withToken) {
  const elements = {};
  const document = {
    getElementById(id) { return elements[id] || (elements[id] = makeEl(id)); },
    querySelectorAll() { return []; },
    querySelector() { return makeEl('qs'); },
    createElement(t) { return makeEl(t + Math.random()); },
    body: { appendChild() {}, style: {} }
  };
  const store = withToken ? { alnathim_token: 'T', alnathim_server: 'https://alnathim.onrender.com' } : {};
  const localStorageMock = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
    clear: () => { for (const k in store) delete store[k]; }
  };
  let calls = 0;
  const fetchMock = async () => ({ status: 200, json: async () => ({ ok: true, data: {} }) });
  const intervals = { n: 0 };
  const sb = {
    window: { location: { pathname: '/index.html', href: '', replace(u) { this.href = u; } }, addEventListener() {}, onNativeResult() {} },
    document, localStorage: localStorageMock, fetch: fetchMock,
    console, setTimeout, clearTimeout,
    setInterval: (fn, ms) => { intervals.n++; return { _fake: true, fn, ms }; },
    clearInterval: () => {},
    URLSearchParams,
  };
  sb.window.document = document;
  sb.location = sb.window.location; // browsers expose global `location`
  sb._intervals = intervals;
  vm.createContext(sb);
  return sb;
}

let pass = 0, fail = 0;
const check = (cond, msg) => { if (cond) { pass++; console.log('[OK  ] ' + msg); } else { fail++; console.log('[FAIL] ' + msg); } };

// ── 1) Every page must LOAD without throwing (auth pages with a token) ──
for (const page of pages) {
  const html = fs.readFileSync(path.join(__dirname, page), 'utf8');
  const inline = (html.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/) || [])[1] || '';
  const isIndex = page === 'index.html';
  const sb = makeSandbox(!isIndex); // non-index pages need a token for requireAuth()
  let threw = null;
  try {
    vm.runInContext(appJs, sb);
    vm.runInContext(inline, sb);
  } catch (e) { threw = e; }
  check(!threw, `${page} loads without runtime error` + (threw ? ` (${threw.message})` : ''));
  check(!isIndex || html.includes('js/app.js'), 'index.html includes js/app.js');
  check(html.includes('js/app.js'), `${page} includes js/app.js`);
}

// ── 2) index.html: pressing the login button completes the flow ──
const indexHtml = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const indexInline = (indexHtml.match(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/) || [])[1] || '';
const sb = makeSandbox(false);
let loginUrl = '';
vm.runInContext(appJs, sb);
vm.runInContext(indexInline, sb);
sb.fetch = async (url, opts) => {
  loginUrl = url;
  if (url.includes('/auth/me')) return { status: 200, json: async () => ({ ok: false, error: { code: 'unauthorized' } }) };
  return { status: 200, json: async () => ({ ok: true, data: { token: 'TEST_TOKEN', user: { username: 'admin' } } }) };
};
(async () => {
  // wait for any restore-check promises, then press the button
  await new Promise(r => setTimeout(r, 50));
  sb.document.getElementById('username').value = 'admin';
  sb.document.getElementById('password').value = 'secret';
  sb.document.getElementById('serverBase').value = 'https://alnathim.onrender.com';
  await sb.doLogin();
  check(loginUrl === 'https://alnathim.onrender.com/api/mobile/v1/auth/login', 'login POST hits the mobile auth endpoint');
  check(sb.localStorage.getItem('alnathim_token') === 'TEST_TOKEN', 'token saved to localStorage');
  check(sb.window.location.href === 'dashboard.html', 'redirects to dashboard');

  // ── 3) pending state → waiting card + polling starts ──
  const sb2 = makeSandbox(false);
  vm.runInContext(appJs, sb2);
  vm.runInContext(indexInline, sb2);
  sb2.fetch = async () => ({ status: 403, json: async () => ({ ok: false, error: { code: 'pending', message_ar: 'بانتظار موافقة المدير' } }) });
  await new Promise(r => setTimeout(r, 50));
  sb2.document.getElementById('username').value = 'admin';
  sb2.document.getElementById('password').value = 'secret';
  sb2.document.getElementById('serverBase').value = 'https://alnathim.onrender.com';
  await sb2.doLogin();
  check(sb2.document.getElementById('waitingCard').classList.contains('hidden') === false, 'pending → waiting card is shown');
  check(sb2._intervals.n >= 1, 'pending → polling timer started (' + sb2._intervals.n + ' interval(s))');

  console.log('\nRuntime page simulation: ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})();
