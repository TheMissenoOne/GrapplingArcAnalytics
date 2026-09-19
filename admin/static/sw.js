// Service worker for the GrapplingArc Admin PWA. Scope is /admin/ (registered from
// base.html at /admin/sw.js, not under /admin/static/, so the default scope already
// covers every admin route — no Service-Worker-Allowed header needed for that, though
// the server sends one anyway as a defensive no-op for browsers that check it strictly).
//
// Bump CACHE_VERSION to invalidate every old cache on the next activate.
const CACHE_VERSION = "ga-admin-v1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const OFFLINE_URL = "/admin/offline";

// Precached at install so the offline fallback + shell CSS work with zero network.
const PRECACHE_URLS = [OFFLINE_URL, "/admin/static/admin.css"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/admin/static/");
}

// Never touch the login gate — a cached login/logout response could let a stale
// authenticated shell render offline, or serve a stale CSRF token.
function isAuthSensitive(url) {
  return url.pathname === "/admin/login" || url.pathname === "/admin/logout";
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never intercept a mutation
  const url = new URL(req.url);
  if (isAuthSensitive(url)) return; // fall through to network, untouched

  if (isStaticAsset(url)) {
    // cache-first for versioned static assets (css/js/icons)
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (cached) return cached;
        const res = await fetch(req);
        if (res.ok) cache.put(req, res.clone());
        return res;
      })
    );
    return;
  }

  // network-first everywhere else (HTML pages, JSON/API routes) — audit data and
  // athlete state must never be served stale; offline only falls back for a
  // navigation, never for a data fetch.
  event.respondWith(
    fetch(req).catch(() => {
      if (req.mode === "navigate") {
        return caches.match(OFFLINE_URL).then((cached) => cached || Response.error());
      }
      return Response.error();
    })
  );
});
