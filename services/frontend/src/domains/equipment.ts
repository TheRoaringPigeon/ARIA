import type { DomainConfig, FieldConfig } from './base'
import type { GeneratedEquipmentAttrs } from './generated'
import { GENERATED } from './generated'

export type EquipmentAttrs = GeneratedEquipmentAttrs

const FIELDS: FieldConfig<EquipmentAttrs>[] = [
  { key: 'make', label: 'Make', kind: 'text' },
  { key: 'model', label: 'Model', kind: 'text' },
  { key: 'serial_number', label: 'Serial number', kind: 'text' },
  { key: 'purchase_date', label: 'Purchase date', kind: 'date' },
]

export const equipmentConfig: DomainConfig<EquipmentAttrs> = {
  label: 'Equipment',
  statuses: GENERATED.equipment.statuses,
  logTypes: GENERATED.equipment.logTypes,
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'equipment' }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
  uiVariant: 'schedule',
}
