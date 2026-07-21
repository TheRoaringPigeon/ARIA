import { precacheAndRoute } from 'workbox-precaching'
import { registerRoute } from 'workbox-routing'
import { NetworkOnly } from 'workbox-strategies'
import { BackgroundSyncPlugin } from 'workbox-background-sync'

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>
}

precacheAndRoute(self.__WB_MANIFEST)

// Baked in at build time — a service worker has no access to page-injected
// globals (e.g. api/client.ts's CORE_API_URL constant), so this has to be
// read from the same env var independently.
const CORE_API_ORIGIN = new URL(import.meta.env.VITE_CORE_API_URL ?? 'http://localhost:8000').origin

const logSyncChannel = new BroadcastChannel('aria-log-sync')

const backgroundSyncPlugin = new BackgroundSyncPlugin('aria-log-create-queue', {
  // Stop retrying (and let the page's own online-triggered fallback drain
  // take over) after 24h rather than retrying forever.
  maxRetentionTime: 24 * 60,
  onSync: async ({ queue }) => {
    let entry
    while ((entry = await queue.shiftRequest())) {
      const localId = entry.request.headers.get('X-Aria-Local-Id')
      try {
        const response = await fetch(entry.request)
        if (response.ok) {
          const log = await response.json()
          if (localId) logSyncChannel.postMessage({ type: 'synced', localId, log })
        } else {
          // A resolved HTTP error (400/401/404/422/...) is terminal — Workbox
          // only re-queues a *thrown* network error, never a response that
          // came back, so this branch already means "don't retry" for free.
          // Just report the outcome back to the page.
          const detail = await response.text()
          if (localId) {
            logSyncChannel.postMessage({ type: 'failed', localId, status: response.status, detail })
          }
        }
      } catch (err) {
        await queue.unshiftRequest(entry)
        throw err
      }
    }
  },
})

registerRoute(
  ({ url, request }) =>
    request.method === 'POST' && url.origin === CORE_API_ORIGIN && url.pathname === '/logs',
  new NetworkOnly({ plugins: [backgroundSyncPlugin] }),
  'POST',
)
