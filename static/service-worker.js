const CACHE_NAME = 'alnathim-v3';
const SHELL_ASSETS = [
  '/',
  '/network',
  '/customers',
  '/debts',
  '/payments',
  '/billing',
  '/report',
  '/more',
  '/settings',
  '/static/manifest.json',
  '/static/service-worker.js'
];

// Install: pre-cache the app shell so first-tap after install is instant.
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => Promise.allSettled(SHELL_ASSETS.map((u) => cache.add(u))))
      .then(() => self.skipWaiting())
  );
});

// Activate: drop old caches and take control immediately.
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

/**
 * Cache-First with background revalidation (the "Blood" pattern):
 * - Any page/asset already in cache is served INSTANTLY (no spinner).
 * - Meanwhile the network re-fetches and updates the cache in the background.
 * - If the network is unavailable, the cached copy is still served.
 * - New data added on the server appears right after the background refresh.
 */
self.addEventListener('fetch', (e) => {
  const { request } = e;
  if (request.method !== 'GET') return;

  // Only cache same-origin navigations and static assets.
  const url = new URL(request.url);
  const isSameOrigin = url.origin === self.location.origin;
  if (!isSameOrigin) return;

  // Skip non-essential requests (e.g. /api/... custom JSON fetches are
  // handled by the pages themselves with localStorage snapshots).
  if (url.pathname.startsWith('/api/')) return;

  e.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok && (response.type === 'basic' || response.type === 'default')) {
            try { cache.put(request, response.clone()); } catch (err) {}
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
