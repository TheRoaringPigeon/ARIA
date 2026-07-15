import { apiDelete, apiGet, apiPatch, apiPost } from './client'
import type { DueScheduleItem, IntervalType, Schedule } from './types'

export interface ScheduleCreateInput {
  entity_id: string
  title: string
  active?: boolean
  interval_type: IntervalType
  interval_days?: number | null
  usage_metric?: string | null
  interval_usage_amount?: number | null
  starting_at?: string | null
  starting_usage_value?: number | null
  planned_at?: string | null
  planned_time?: string | null
  monthly_day?: number | null
  monthly_weekday?: number | null
  monthly_week_index?: number | null
}

export interface ScheduleUpdateInput {
  title?: string
  active?: boolean
  interval_type?: IntervalType
  interval_days?: number | null
  usage_metric?: string | null
  interval_usage_amount?: number | null
  planned_at?: string | null
  planned_time?: string | null
  monthly_day?: number | null
  monthly_weekday?: number | null
  monthly_week_index?: number | null
}

export function listEntitySchedules(entityId: string): Promise<Schedule[]> {
  return apiGet<Schedule[]>(`/entities/${entityId}/schedules`)
}

export function createSchedule(input: ScheduleCreateInput): Promise<Schedule> {
  return apiPost<Schedule>('/schedules', input)
}

export function updateSchedule(id: string, input: ScheduleUpdateInput): Promise<Schedule> {
  return apiPatch<Schedule>(`/schedules/${id}`, input)
}

export function deleteSchedule(id: string): Promise<void> {
  return apiDelete(`/schedules/${id}`)
}

export function listDueSoon(withinDays?: number): Promise<DueScheduleItem[]> {
  const qs = withinDays !== undefined ? `?within_days=${withinDays}` : ''
  return apiGet<DueScheduleItem[]>(`/schedules/due-soon${qs}`)
}
