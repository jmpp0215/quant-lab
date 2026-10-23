// App shell is cached so the page opens while the Mac is asleep; data.json is
// never cached here - index.html keeps the last good copy in localStorage.
const CACHE = "tracker-v16";
const SHELL = ["./", "index.html", "manifest.json", "icon-180.png", "icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys =>
    Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});

// Network first so a changed page shows up at once, but only briefly: with
// the Mac asleep a request over Tailscale doesn't fail, it hangs, so after
// NETWORK_WAIT_MS the cached copy is served instead (and the network answer,
// if it ever comes, still refreshes the cache).
const NETWORK_WAIT_MS = 3000;

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.endsWith("/data.json")) return;
  const network = fetch(e.request).then(res => {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(e.request, copy));
    return res;
  });
  const fallback = new Promise(resolve => setTimeout(resolve, NETWORK_WAIT_MS))
    .then(() => caches.match(e.request))
    .then(hit => hit || network);
  e.respondWith(Promise.race([network.catch(() => caches.match(e.request)), fallback]));
});
