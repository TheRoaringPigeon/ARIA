import { createStore, del, get, set, values } from 'idb-keyval'
import type { LogCreateInput } from '../api/logs'

export interface PendingLogRecord {
  localId: string
  input: LogCreateInput
  entityId: string
  queuedAt: string
  status: 'pending' | 'failed'
  errorStatus?: number
  errorMessage?: string
}

// A separate IndexedDB database from whatever Workbox's own
// workbox-background-sync Queue uses internally for actual request replay
// (that storage is private/opaque to app code). This store is purely the
// UI-facing "what does the user see" source of truth.
const store = createStore('aria-pending-logs', 'records')

export async function getAllPendingLogs(): Promise<PendingLogRecord[]> {
  return values<PendingLogRecord>(store)
}

export async function getPendingLogsForEntity(entityId: string): Promise<PendingLogRecord[]> {
  const all = await getAllPendingLogs()
  return all.filter((record) => record.entityId === entityId)
}

export async function addPendingLog(record: PendingLogRecord): Promise<void> {
  await set(record.localId, record, store)
}

export async function markPendingLogFailed(
  localId: string,
  errorStatus: number,
  errorMessage: string,
): Promise<void> {
  const existing = await get<PendingLogRecord>(localId, store)
  if (!existing) return
  await set(localId, { ...existing, status: 'failed', errorStatus, errorMessage }, store)
}

export async function removePendingLog(localId: string): Promise<void> {
  await del(localId, store)
}
