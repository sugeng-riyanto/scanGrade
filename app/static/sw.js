const CACHE = 'scangrade-v2';
const STATIC = [
  '/static/manifest.json',
  '/static/icon.svg',
  '/static/js/tools.js',
];

// Install: cache static assets only
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).catch(() => {})
  );
});

// Activate: clean old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(k => k !== CACHE).map(k => caches.delete(k))
    ))
  );
});

// Fetch: network-first for API, cache-first for static
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  // API requests: network only
  if (u.pathname.startsWith('/api/')) {
    e.respondWith(fetch(e.request).catch(() => new Response(JSON.stringify({offline:true}), {status:503})));
    return;
  }
  // Static: cache-first
  if (u.pathname.startsWith('/static/')) {
    e.respondWith(
      caches.match(e.request).then(r => r || fetch(e.request).catch(() => r))
    );
    return;
  }
  // HTML/others: network-first, only cache successful responses
  e.respondWith(
    fetch(e.request).then(r => {
      if (r.ok) {
        const clone = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});
