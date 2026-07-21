import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import * as api from '../api/logs'
import { ApiError } from '../api/client'
import { getAllPendingLogs, markPendingLogFailed, removePendingLog } from '../lib/pendingLogs'
import type { LogEntry } from '../api/types'

const SYNC_TAG = 'aria-log-create-queue'

// Background Sync API types aren't in TS's standard DOM lib (still
// non-standard) — a minimal local shape for the one method actually used.
interface SyncCapableRegistration extends ServiceWorkerRegistration {
  sync: { register(tag: string): Promise<void> }
}

interface SyncMessage {
  type: 'synced' | 'failed'
  localId: string
  log?: LogEntry
  status?: number
  detail?: string
}

// Mounted once (in Layout.tsx) for the lifetime of the app. Bridges the
// service worker's out-of-band replay (src/sw.ts) back to the page: keeps
// the idb-keyval pending-log store and TanStack Query cache in sync with
// what actually happened, and — for browsers without the Background Sync
// API (Safari, Firefox) — owns replay itself via a plain 'online' listener.
export function useLogSyncListener() {
  const queryClient = useQueryClient()

  useEffect(() => {
    async function handleSynced(localId: string, log: LogEntry | undefined) {
      await removePendingLog(localId)
      if (!log) return
      queryClient.invalidateQueries({ queryKey: ['pending-logs', log.entity_id] })
      queryClient.invalidateQueries({ queryKey: ['logs', log.entity_id] })
      if (log.schedule_id) {
        queryClient.invalidateQueries({ queryKey: ['schedules', log.entity_id] })
        queryClient.invalidateQueries({ queryKey: ['due-soon'] })
      }
    }

    async function handleFailed(localId: string, status: number | undefined, detail: string | undefined) {
      await markPendingLogFailed(localId, status ?? 0, detail ?? 'Sync failed')
      const record = (await getAllPendingLogs()).find((r) => r.localId === localId)
      if (record) queryClient.invalidateQueries({ queryKey: ['pending-logs', record.entityId] })
    }

    let channel: BroadcastChannel | undefined
    if ('BroadcastChannel' in window) {
      channel = new BroadcastChannel('aria-log-sync')
      channel.onmessage = (event: MessageEvent<SyncMessage>) => {
        const msg = event.data
        if (msg.type === 'synced') void handleSynced(msg.localId, msg.log)
        else void handleFailed(msg.localId, msg.status, msg.detail)
      }
    }

    // No service worker in this browser at all (or it never registered) —
    // drain the queue directly from the page instead of via Workbox.
    async function drainQueueManually() {
      const pending = (await getAllPendingLogs()).filter((r) => r.status === 'pending')
      for (const record of pending) {
        try {
          const log = await api.createLog(record.input, { localId: record.localId })
          await handleSynced(record.localId, log)
        } catch (err) {
          if (err instanceof ApiError) {
            await handleFailed(record.localId, err.status, err.message)
          }
          // Otherwise still offline (NetworkError) — leave it queued and
          // retry on the next 'online' event rather than surface a failure.
        }
      }
    }

    function handleOnline() {
      if ('serviceWorker' in navigator && 'SyncManager' in window) {
        // The SW's queue is authoritative here; this is a cheap idempotent
        // nudge in case the native trigger is slow or gets missed, not the
        // primary replay path.
        navigator.serviceWorker.ready
          .then((registration) => (registration as SyncCapableRegistration).sync.register(SYNC_TAG))
          .catch(() => void drainQueueManually())
      } else {
        void drainQueueManually()
      }
    }

    window.addEventListener('online', handleOnline)
    return () => {
      window.removeEventListener('online', handleOnline)
      channel?.close()
    }
  }, [queryClient])
}
