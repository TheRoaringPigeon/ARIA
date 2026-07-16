import type { DomainConfig, FieldConfig } from './base'
import type { GeneratedProjectAttrs } from './generated'
import { GENERATED } from './generated'

export type ProjectAttrs = GeneratedProjectAttrs

const FIELDS: FieldConfig<ProjectAttrs>[] = [
  { key: 'start_date', label: 'Start date', kind: 'date' },
  { key: 'target_end_date', label: 'Target end date', kind: 'date' },
  { key: 'completed_date', label: 'Completed date', kind: 'date' },
  { key: 'budget_estimate', label: 'Budget estimate', kind: 'number' },
]

export const projectConfig: DomainConfig<ProjectAttrs> = {
  label: 'Project',
  statuses: GENERATED.project.statuses,
  logTypes: GENERATED.project.logTypes,
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'project', related_entity_ids: [] }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
  uiVariant: 'schedule',
}
