export type EntityDomain = 'home' | 'vehicle' | 'equipment' | 'project'

export interface HomeAttrs {
  domain: 'home'
  entity_type: 'room' | 'system' | 'appliance' | 'structure'
  make?: string | null
  model?: string | null
  serial_number?: string | null
  paint_brand?: string | null
  paint_code?: string | null
  install_date?: string | null
  warranty_expires_at?: string | null
}

export interface VehicleAttrs {
  domain: 'vehicle'
  make: string
  model: string
  year: number
  vin?: string | null
  license_plate?: string | null
  current_mileage?: number | null
  purchase_date?: string | null
}

export interface EquipmentAttrs {
  domain: 'equipment'
  make?: string | null
  model?: string | null
  serial_number?: string | null
  purchase_date?: string | null
}

export interface ProjectAttrs {
  domain: 'project'
  related_entity_ids: string[]
  start_date?: string | null
  target_end_date?: string | null
  completed_date?: string | null
  budget_estimate?: number | null
}

export type EntityAttributes = HomeAttrs | VehicleAttrs | EquipmentAttrs | ProjectAttrs

export const STATUS_BY_DOMAIN: Record<EntityDomain, string[]> = {
  home: ['active', 'needs_attention', 'archived'],
  vehicle: ['active', 'in_service', 'sold', 'archived'],
  equipment: ['active', 'in_service', 'retired'],
  project: ['planning', 'in_progress', 'on_hold', 'completed'],
}

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

export type LogType = 'service' | 'repair' | 'inspection' | 'expense' | 'note' | 'milestone'

export const LOG_TYPES: LogType[] = ['service', 'repair', 'inspection', 'expense', 'note', 'milestone']

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

export type IntervalType = 'time' | 'usage'

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
