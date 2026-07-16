import type { DomainConfig, FieldConfig } from './base'
import type { GeneratedHomeAttrs } from './generated'
import { GENERATED } from './generated'

export type HomeAttrs = GeneratedHomeAttrs

const FIELDS: FieldConfig<HomeAttrs>[] = [
  {
    key: 'entity_type',
    label: 'Type',
    kind: 'select',
    required: true,
    options: GENERATED.home.literalOptions.entity_type,
  },
  { key: 'make', label: 'Make', kind: 'text' },
  { key: 'model', label: 'Model', kind: 'text' },
  { key: 'serial_number', label: 'Serial number', kind: 'text' },
  { key: 'paint_brand', label: 'Paint brand', kind: 'text' },
  { key: 'paint_code', label: 'Paint code', kind: 'text' },
  { key: 'install_date', label: 'Install date', kind: 'date' },
  { key: 'warranty_expires_at', label: 'Warranty expires', kind: 'date' },
]

export const homeConfig: DomainConfig<HomeAttrs, 'home'> = {
  label: 'Home',
  statuses: GENERATED.home.statuses,
  logTypes: GENERATED.home.logTypes,
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'home', entity_type: 'room' }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
  uiVariant: 'schedule',
}
