const CACHE_NAME = 'es90-sales-app-v123';
const APP_SHELL = ['./', './index.html', './app.html', './manifest.webmanifest', './icons/es90-icon.svg', './assets/es90-login-cover-lights-on-v3-led.png', './assets/es90-headlight-shape-mask-v1.png'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin) return;

  // 앱 화면과 탐색 요청은 항상 온라인 최신본을 우선 사용하고, 오프라인일 때만 저장본을 사용한다.
  if (event.request.mode === 'navigate' || url.pathname.endsWith('/app.html') || url.pathname.endsWith('/version.json')) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then(response => {
          if (response.ok && !url.pathname.endsWith('/version.json')) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
          }
          return response;
        })
        .catch(() => caches.match(event.request).then(hit => hit || caches.match('./app.html')))
    );
    return;
  }

  event.respondWith(
    caches.match(event.request).then(hit => hit || fetch(event.request).then(response => {
      if (response.ok) caches.open(CACHE_NAME).then(cache => cache.put(event.request, response.clone()));
      return response;
    }))
  );
});
