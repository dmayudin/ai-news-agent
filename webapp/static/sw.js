// Service Worker for AI News PWA
// Стратегия: кэшируем статику (shell), API-запросы всегда идут в сеть

const CACHE_NAME = 'ai-news-v2';
const SHELL_ASSETS = [
  '/',
  '/icons/icon-192.png?v=2',
  '/icons/icon-512.png?v=2',
  '/manifest.json'
];

// Установка: кэшируем shell
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(SHELL_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// Активация: удаляем старые кэши (включая ai-news-v1)
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

// Fetch: API всегда в сеть, статика из кэша с fallback в сеть
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API-запросы, логин/логаут — всегда в сеть, никогда не кэшируем
  if (url.pathname.startsWith('/api/') ||
      url.pathname === '/login' ||
      url.pathname === '/logout') {
    event.respondWith(fetch(event.request));
    return;
  }

  // Статика: cache-first, fallback в сеть
  event.respondWith(
    caches.match(event.request).then(cached => {
      return cached || fetch(event.request).then(response => {
        // Кэшируем только успешные GET-ответы на статику
        if (event.request.method === 'GET' && response.status === 200) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        }
        return response;
      });
    })
  );
});
