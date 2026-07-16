import type { EntityAttributes, EntityDomain, LogType } from '../domains'

export type { LogType }

export interface Entity {
  id: string
  household_id: string
  domain: EntityDomain
  name: string
  status: string
  tags: string[]
  location: string | null
  specs: Record<string, string>
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

export interface SessionInfo {
  household_id: string
  user_id: string
  user_name: string
}
