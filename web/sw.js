/* Estrategia de caché.
 *
 * La navegación va a red primero: un service worker cache-first sobre el shell
 * significa que el usuario nunca ve una actualización, que es exactamente el
 * bug que este archivo tenía. La caché queda como respaldo sin conexión.
 *
 * Los estáticos van cache-first porque llevan hash de contenido en la URL: si
 * cambian, cambia la URL, así que una entrada vieja nunca puede quedar obsoleta.
 *
 * La API no se cachea nunca. Un precio viejo mostrado como actual es peor que
 * no mostrar precio, y el encabezado ya informa la edad del dato.
 */
const SHELL = 'fa-shell-v3';
const FILES = [
  '/',
  '/static/icon.svg',
  '/static/vendor/uPlot.iife.min.js',
  '/static/vendor/uPlot.min.css',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const copy = response.clone();
          caches.open(SHELL).then((c) => c.put('/', copy));
          return response;
        })
        .catch(() => caches.match('/'))
    );
    return;
  }

  event.respondWith(
    caches.match(request).then((hit) => hit || fetch(request).then((response) => {
      if (response.ok && url.pathname.startsWith('/static/')) {
        const copy = response.clone();
        caches.open(SHELL).then((c) => c.put(request, copy));
      }
      return response;
    }))
  );
});
