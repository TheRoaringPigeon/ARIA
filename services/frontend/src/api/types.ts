import type { EntityAttributes, EntityDomain, LogType } from '../domains'

export type { LogType }

// The whole household (the default — matches this app's behavior before
// per-record sharing existed) or a specific subset of its members' user ids.
export type SharedWith = 'household' | string[]

export interface Entity {
  id: string
  household_id: string
  domain: EntityDomain
  name: string
  status: string
  tags: string[]
  location: string | null
  specs: Record<string, string>
  shared_with: SharedWith
  created_by: string
  created_at: string
  updated_at: string
  archived_at: string | null
  attributes: EntityAttributes
}

export interface LogEntry {
  id: string
  household_id: string
  entity_id: string
  domain: EntityDomain
  type: LogType
  occurred_at: string
  title: string
  description: string | null
  cost: number | null
  metrics: Record<string, string>
  document_ids: string[]
  schedule_id: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export type IntervalType = 'time' | 'usage' | 'once' | 'monthly'

export interface Schedule {
  id: string
  household_id: string
  entity_id: string
  domain: EntityDomain
  title: string
  active: boolean
  interval_type: IntervalType
  interval_days: number | null
  usage_metric: string | null
  interval_usage_amount: number | null
  planned_at: string | null
  planned_time: string | null
  monthly_day: number | null
  monthly_weekday: number | null
  monthly_week_index: number | null
  last_completed_log_id: string | null
  last_completed_at: string | null
  last_completed_usage_value: number | null
  next_due_at: string | null
  next_due_usage_value: number | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface DueScheduleItem {
  schedule: Schedule
  entity_name: string
  is_overdue: boolean
}

export type DocumentType = 'manual' | 'receipt' | 'invoice' | 'photo' | 'diagram' | 'other'
export type ProcessingStatus = 'pending' | 'ocr_complete' | 'chunked' | 'embedded' | 'failed'

export interface Document {
  id: string
  household_id: string
  entity_ids: string[]
  log_ids: string[]
  document_type: DocumentType
  original_filename: string
  storage_path: string
  mime_type: string
  file_size_bytes: number
  page_count: number | null
  processing_status: ProcessingStatus
  processing_error: string | null
  shared_with: SharedWith
  uploaded_by: string
  uploaded_at: string
}

export interface SessionInfo {
  household_id: string
  user_id: string
  user_name: string
  role: 'owner' | 'member'
}

export interface Member {
  id: string
  name: string
  email: string
  role: 'owner' | 'member'
}

export interface Household {
  id: string
  name: string
  city: string | null
}

export interface Invite {
  token: string
  expires_at: string
}
