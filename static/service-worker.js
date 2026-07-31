const CACHE_NAME = 'mawlidati-v1';
const ASSETS = [
  '/',
  '/customers',
  '/debts',
  '/payments',
  '/billing',
  '/settings',
  '/static/manifest.json'
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
});

self.addEventListener('fetch', (e) => {
  e.respondWith(
    caches.match(e.request).then((res) => res || fetch(e.request))
  );
});