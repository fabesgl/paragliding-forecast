// Service worker for the Mystic forecast app.
// The page and forecast.json: network first, so a new forecast always replaces the cached one when you are online.
//   The request revalidates with the server (no stale browser-cache copy). If the network is slow (>3.5 s) or down, the cached copy is shown,
//   and a slow response still updates the cache in the background for next time.
// Icons and the manifest: cache first (they rarely change).
const CACHE = "mystic-v1";
const SCOPE = self.registration.scope;                       // e.g. https://user.github.io/paragliding-forecast/
const PAGE = new URL("./", SCOPE).href;
const PRECACHE = ["./", "manifest.webmanifest", "forecast.json", "icons/icon-192.png", "icons/icon-512.png", "icons/maskable-192.png",
                  "icons/maskable-512.png", "icons/apple-touch-icon.png", "icons/favicon-32.png"].map(p => new URL(p, SCOPE).href);

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(async c => {
    for (const u of PRECACHE) { try { await c.add(new Request(u, {cache: "reload"})); } catch (_) {} }   // one missing file must not block install
  }).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

function keyFor(req) {                                         // all ways of opening the page share one cache entry
  const u = new URL(req.url);
  if (req.mode === "navigate" || u.pathname.endsWith("/") || u.pathname.endsWith("/index.html")) return PAGE;
  return req.url;
}

function stamped(res) {                                        // marks a response served from the cache because the network failed or was slow
  const h = new Headers(res.headers); h.set("x-sw-cache", "1");
  return new Response(res.body, {status: res.status, statusText: res.statusText, headers: h});
}

async function networkFirst(req) {
  const cache = await caches.open(CACHE), key = keyFor(req);
  const net = fetch(req.mode === "navigate" ? PAGE : req.url, {cache: "no-cache"}).then(async res => {
    if (res && res.ok) await cache.put(key, res.clone());
    return res;
  });
  const cached = await cache.match(key);
  if (!cached) return net;                                      // nothing cached yet: just wait for the network
  const timeout = new Promise(r => setTimeout(() => r(null), 3500));
  try {
    const res = await Promise.race([net, timeout]);
    return res || stamped(cached);                              // slow network: show the cached copy; `net` still finishes and refreshes the cache
  } catch (_) { return stamped(cached); }                       // offline
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE), hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res && res.ok) cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request, u = new URL(req.url);
  if (req.method !== "GET" || u.origin !== location.origin || !u.href.startsWith(SCOPE)) return;
  const dynamic = req.mode === "navigate" || u.pathname.endsWith("/") || u.pathname.endsWith("/index.html") || u.pathname.endsWith("/forecast.json");
  e.respondWith(dynamic ? networkFirst(req) : cacheFirst(req));
});
