import type { DomainConfig, FieldConfig } from './base'

export interface EquipmentAttrs {
  domain: 'equipment'
  make?: string | null
  model?: string | null
  serial_number?: string | null
  purchase_date?: string | null
}

const FIELDS: FieldConfig[] = [
  { key: 'make', label: 'Make', kind: 'text' },
  { key: 'model', label: 'Model', kind: 'text' },
  { key: 'serial_number', label: 'Serial number', kind: 'text' },
  { key: 'purchase_date', label: 'Purchase date', kind: 'date' },
]

export const equipmentConfig: DomainConfig<EquipmentAttrs> = {
  label: 'Equipment',
  statuses: ['active', 'in_service', 'retired'],
  logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'],
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'equipment' }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
}
