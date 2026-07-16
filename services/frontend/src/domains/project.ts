import type { DomainConfig, FieldConfig } from './base'

export interface ProjectAttrs {
  domain: 'project'
  related_entity_ids: string[]
  start_date?: string | null
  target_end_date?: string | null
  completed_date?: string | null
  budget_estimate?: number | null
}

const FIELDS: FieldConfig[] = [
  { key: 'start_date', label: 'Start date', kind: 'date' },
  { key: 'target_end_date', label: 'Target end date', kind: 'date' },
  { key: 'completed_date', label: 'Completed date', kind: 'date' },
  { key: 'budget_estimate', label: 'Budget estimate', kind: 'number' },
]

export const projectConfig: DomainConfig<ProjectAttrs> = {
  label: 'Project',
  statuses: ['planning', 'in_progress', 'on_hold', 'completed'],
  logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'],
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'project', related_entity_ids: [] }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
}
