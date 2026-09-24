/* nookboard's service worker: an offline answer, never a faster one.
 *
 * This app sends `no-store` on every response and has no asset versioning, so a
 * cache here could only ever be a copy of something that has since been fixed --
 * twice now that has been the whole of a bug report (a stale `app.js`, a stale
 * vendored `lucide.js`, whose icons looked clipped only in the tab that had been
 * open the longest). So the rule is **network-first everywhere**: if the server
 * answers, its answer is what you get, and the cache is consulted only when
 * there is nothing left to ask.
 *
 * The cache fills as you use the app rather than from a list of files. A
 * precache list is a second inventory of the app that nothing keeps in step with
 * the first, and this app has seventeen JS modules and a vendored icon set;
 * whatever you have actually opened is what works offline, which is the honest
 * promise and needs nothing maintained.
 *
 * `/api/` is never cached. A notes app that shows you a remembered answer to
 * "what is in my vault" is lying, and this one would rather say it cannot reach
 * the server.
 */

const CACHE = "nookboard-v1";

self.addEventListener("install", () => {
  // There is no precache list to fetch, so there is nothing to wait for: take
  // over as soon as the browser will let us.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      // A new worker means new behaviour, so an old cache is not a fallback --
      // it is the stale copy this worker exists to stop serving.
      const names = await caches.keys();
      await Promise.all(
        names.filter((name) => name !== CACHE).map((name) => caches.delete(name))
      );
      await self.clients.claim();
    })()
  );
});

/** A response we are willing to hand back while the server is unreachable.
 *
 * Only a real success is worth keeping: caching an error page means being stuck
 * with it, and a `no-store` response is still storable here (the Cache API does
 * not consult it) -- which is what makes offline work at all in an app that
 * refuses to be cached by the browser.
 */
async function keep(request, response) {
  if (!response || !response.ok || response.type !== "basic") return response;
  try {
    const cache = await caches.open(CACHE);
    await cache.put(request, response.clone());
  } catch {
    /* a full disk or a hostile quota is not a reason to fail the request */
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return; // not ours to answer for
  if (url.pathname.startsWith("/api/")) return; // never remember an answer

  event.respondWith(
    (async () => {
      try {
        return await keep(request, await fetch(request));
      } catch (err) {
        const remembered = await caches.match(request);
        if (remembered) return remembered;

        // A navigation to somewhere never visited has nothing cached. The app
        // itself is the honest fallback; without it, say plainly what happened
        // rather than letting the browser show its own dinosaur.
        if (request.mode === "navigate") {
          const shell = (await caches.match("/")) || (await caches.match("/index.html"));
          if (shell) return shell;
        }
        return new Response(
          "nookboard cannot be reached, and this page has not been loaded before.\n",
          { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } }
        );
      }
    })()
  );
});
