import { apiGet, apiPost } from './client'
import type { LogEntry, LogType } from './types'

export interface LogCreateInput {
  entity_id: string
  type: LogType
  occurred_at: string
  title: string
  description?: string | null
  cost?: number | null
  metrics?: Record<string, string>
  document_ids?: string[]
  schedule_id?: string | null
}

export function listEntityLogs(entityId: string): Promise<LogEntry[]> {
  return apiGet<LogEntry[]>(`/entities/${entityId}/logs`)
}

export function createLog(input: LogCreateInput): Promise<LogEntry> {
  return apiPost<LogEntry>('/logs', input)
}
