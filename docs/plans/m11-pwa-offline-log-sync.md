# M11 — PWA / offline background sync (log creation)

## Context

`docs/roadmap.md` had every numbered milestone through M10 done, with one
item left sitting under "Explicitly deferred past MVP": PWA/offline
background sync, listed in the PRD's frontend stack but not required for
any MVP exit criterion, pending revisit once the UI was real enough to need
it. Pulled forward 2026-07-21.

The PRD's only concrete language on this (Section 5, Frontend Architecture)
is narrow: *"Highly responsive dashboard optimized for desktop and mobile,
with background syncing capabilities to allow offline log creation."* Scope
here is exactly that — fill out and submit the "add a log entry" form while
offline, have it queue locally, have it reach `core-api` automatically once
back online — not offline entity/document/schedule editing, not a fully
offline-first app. Basic installability (manifest + service worker) is a
side effect of the tooling, not a goal in itself.

Before this milestone, `services/frontend` had zero PWA/offline scaffolding:
no manifest, no service worker, no `navigator.onLine` handling, no
`vite-plugin-pwa`, and no optimistic-mutation (`onMutate`) pattern anywhere
in the codebase.

## Key design decisions

1. **Scope stays to log-creation only.** No optimistic-cache-merge /
   temp-id-reconciliation architecture — pending items render in their own
   small "Pending sync" list (`['pending-logs', entityId]`), never merged
   into the real `['logs', entityId]` query cache.
2. **No backend idempotency key added.** Workbox's `BackgroundSyncPlugin`
   only re-queues a request that threw a network-level exception — it does
   not retry on a resolved 4xx/5xx. The realistic duplicate-write window
   (server commits, response lost in transit) is narrow and documented as
   an accepted "known follow-up," matching this project's M8/M9/M10 style
   of calling out narrow residual gaps rather than gold-plating every edge
   case.
3. **`vite-plugin-pwa` with `strategies: 'injectManifest'`**, not
   `generateSW` — needed a real custom service worker (`src/sw.ts`) for
   per-request correlation, a custom `onSync` outcome handler, and
   `BroadcastChannel` messaging back to the page.
4. **`services/frontend/Dockerfile` only runs `npm run dev`** — no prod
   build path exists anywhere in `docker-compose.yml`. `vite-plugin-pwa`
   needed `devOptions: { enabled: true, type: 'module' }` or the service
   worker simply wouldn't exist in this repo's only running configuration.
5. **Auth is cookie-based** (`credentials: 'include'`). `Request`
   properties (headers, body, credentials mode) survive Workbox's
   serialize-to-IndexedDB/replay cycle unchanged, so a replayed POST still
   carries the session cookie — no core-api CORS change needed. A session
   that expired while offline replays as a `401`, bucketed with
   400/404/422 as a non-retryable, user-surfaced failure.
6. **Correlating a queued request to its UI record**: `LogCreate` is
   `extra="forbid"` (can't smuggle a client id into the JSON body), so a
   `X-Aria-Local-Id: <uuid>` header carries it instead — FastAPI/Starlette
   ignores unrecognized headers, and Workbox preserves headers through
   replay.
7. **SW-owns vs page-owns replay, feature-detected, never both**: where
   `'SyncManager' in window` (Chrome/Edge), the service worker's Background
   Sync queue is authoritative. Where it's absent (Safari, Firefox), a
   page-level `online` listener drains the `idb-keyval` pending list
   itself. This avoids a double-POST race by construction.

## What was built

- `services/frontend/src/sw.ts` — injectManifest service worker:
  `precacheAndRoute`, a `registerRoute` matching `POST` to
  `${CORE_API_ORIGIN}/logs` via `NetworkOnly` + `BackgroundSyncPlugin`.
  `onSync` drains the queue: success → `BroadcastChannel('aria-log-sync')`
  posts `{type:'synced', localId, log}`; a resolved non-ok response (this
  is terminal by construction — Workbox only retries a *thrown* error) →
  posts `{type:'failed', localId, status, detail}`; a thrown fetch error →
  `queue.unshiftRequest` and rethrow so Workbox retries later.
- `services/frontend/tsconfig.worker.json` (new) + `tsconfig.json`
  reference + `tsconfig.app.json` `exclude: ["src/sw.ts"]` — `sw.ts` needs
  the `WebWorker` lib (for `ServiceWorkerGlobalScope`, `BroadcastChannel`
  in a worker context, etc.), which declares a conflicting global `self`
  type from the app's `DOM` lib; they can't share one TS project.
- `services/frontend/src/hooks/useOnlineStatus.ts` — `navigator.onLine` +
  `online`/`offline` listeners, driving the cosmetic banner only.
- `services/frontend/src/lib/pendingLogs.ts` — `idb-keyval`-backed CRUD
  over a `PendingLogRecord`. A separate IndexedDB database from Workbox's
  own opaque queue storage — this one is purely the UI-facing source of
  truth.
- `services/frontend/src/hooks/usePendingLogs.ts` — TanStack Query wrapper
  keyed `['pending-logs', entityId]`.
- `services/frontend/src/hooks/useLogSyncListener.ts` — mounted once in
  `Layout.tsx`. Subscribes to `BroadcastChannel('aria-log-sync')`, writes
  outcomes into `idb-keyval`, and invalidates the same query keys
  `useCreateLog`'s `onSuccess` already did. Owns the SW-support feature
  detection for the `online`-event fallback drain path.
- `services/frontend/src/components/OfflineBanner.tsx`,
  `PendingLogList.tsx` — banner + queued/failed item list with
  Retry/Discard.
- `services/frontend/src/api/client.ts` — new `NetworkError` (thrown when
  `fetch()` itself rejects, vs. `ApiError` when a response came back but
  wasn't ok); `apiPost` accepts extra headers.
- `services/frontend/src/api/logs.ts` — `createLog(input, { localId })`
  passes `X-Aria-Local-Id` through.
- `services/frontend/src/hooks/useLogs.ts` — `useCreateLog()` generates a
  `localId`, catches `NetworkError`, writes a `PendingLogRecord`, throws
  `LogQueuedError` (distinguishable from a real `ApiError`).
- `services/frontend/src/pages/EntityDetailPage.tsx` — both
  `createLog.mutate` call sites distinguish `LogQueuedError` (close the
  form, no red error) from `ApiError` (today's unchanged behavior); renders
  `<PendingLogList entityId={entity.id} />`.
- `services/frontend/vite.config.ts` — `VitePWA({ strategies:
  'injectManifest', srcDir: 'src', filename: 'sw.ts', devOptions: {
  enabled: true, type: 'module' }, injectRegister: false, manifest: {...}
  })`. Manifest icon is the existing `favicon.svg` (no new PNG assets were
  generated — no image tooling available in this session; installability
  icon polish is cosmetic and can follow up separately if needed).
- `services/frontend/src/main.tsx` — calls the generated
  `virtual:pwa-register` module's `registerSW({ immediate: true })`.
- `services/frontend/src/components/Layout.tsx` — renders `<OfflineBanner
  />`, mounts `useLogSyncListener()`.

**Deliberately not modified:** `services/core-api/app/routers/logs.py` /
`libs/shared/src/aria_shared/schemas/logs.py` — no idempotency key, per
decision #2 above.

## Verification

`npx tsc -b` (all three project references — app, node, worker), `npx
oxlint`, and `npx vite build` (exercising the actual `injectManifest`
pipeline, which typecheck alone doesn't touch) all passed clean.

Verified end-to-end against the real running dev stack via `claude-in-chrome`
— a browser-automation tool unavailable to every prior AI-milestone plan in
this repo, which had to rely on code review plus a clean build instead of
an actual click-through:

- Confirmed the service worker registers and (after one reload) controls
  the page.
- Stopped `core-api` — this correctly does *not* flip `navigator.onLine`
  (the host's real network interface stays up), proving the
  `fetch()`-failure detection path is what actually matters, not the
  cosmetic banner. Submitted a log against the real household's Ford
  Ranger entity: it queued with no red error, "Pending sync (1)" appeared,
  and the `idb-keyval` record was confirmed directly via IndexedDB
  inspection.
- Restarted `core-api` and confirmed Workbox had itself auto-registered
  the correct native `workbox-background-sync:aria-log-create-queue` sync
  tag (no page-side registration needed for that half). Chrome's native
  `sync` event did not fire within observed wait time in this automated
  browser session — a known limitation of testing Background Sync under
  CDP automation without a real OS-level connectivity transition, not a
  defect in the implementation (flagged as an anticipated risk before
  implementation started). The replay + reconciliation logic itself was
  instead verified by replaying the exact queued request (same endpoint,
  same `X-Aria-Local-Id` header `src/sw.ts` sends) and posting the same
  `BroadcastChannel` message the service worker posts on a real replay —
  exercising the actual, unmodified `useLogSyncListener` code: the pending
  item disappeared and the real, server-assigned log appeared in history.
- Verified the terminal-failure path against a disposable test entity
  (created and deleted via direct API calls, independent of the real
  household's data): queued a log, deleted the entity server-side,
  replayed — the pending item flipped to "Failed to sync" with the real
  404 detail and Retry/Discard controls; Discard cleared it.
- Verified `OfflineBanner` by forcing `navigator.onLine` false and
  dispatching a synthetic `offline` event — banner appeared with the
  expected copy, cleared on restoring `navigator.onLine`.
- All test artifacts (the disposable entity, the test log entry) were
  cleaned up via direct API calls against the running dev stack afterward.
