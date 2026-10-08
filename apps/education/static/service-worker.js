const CACHE_NAME = "maxcim-static-v4";
const STATIC_ASSETS = [
  "/static/css/dashboard.css",
  "/static/js/dashboard.js",
  "/static/js/material.js",
  "/static/js/pwa.js",
  "/static/icons/maxcim.svg",
  "/static/icons/maxcim-192.png",
  "/static/icons/maxcim-512.png",
  "/static/offline.html",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)),
    )),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== "GET" || requestUrl.origin !== self.location.origin) return;

  // Institutional and student information must never be persisted by the PWA cache.
  if (
    requestUrl.pathname.startsWith("/api/") ||
    requestUrl.pathname.startsWith("/media/") ||
    requestUrl.pathname.startsWith("/aulas") ||
    requestUrl.pathname.startsWith("/static/uploads/")
  ) {
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match("/static/offline.html")));
    return;
  }

  if (requestUrl.pathname.startsWith("/static/")) {
    // Network-first: online siempre sirve el CSS/JS fresco y refresca el cache;
    // offline cae al cache. Antes era cache-first y había que subir CACHE_NAME
    // en cada deploy para que el navegador viera los cambios de estilo.
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request)),
    );
  }
});
