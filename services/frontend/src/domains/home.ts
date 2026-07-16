import type { DomainConfig, FieldConfig } from './base'

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

const FIELDS: FieldConfig[] = [
  {
    key: 'entity_type',
    label: 'Type',
    kind: 'select',
    required: true,
    options: ['room', 'system', 'appliance', 'structure'],
  },
  { key: 'make', label: 'Make', kind: 'text' },
  { key: 'model', label: 'Model', kind: 'text' },
  { key: 'serial_number', label: 'Serial number', kind: 'text' },
  { key: 'paint_brand', label: 'Paint brand', kind: 'text' },
  { key: 'paint_code', label: 'Paint code', kind: 'text' },
  { key: 'install_date', label: 'Install date', kind: 'date' },
  { key: 'warranty_expires_at', label: 'Warranty expires', kind: 'date' },
]

export const homeConfig: DomainConfig<HomeAttrs> = {
  label: 'Home',
  statuses: ['active', 'needs_attention', 'archived'],
  logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'],
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'home', entity_type: 'room' }),
  namePlaceholder: 'Display name',
  locationPlaceholder: 'Garage, kitchen, ...',
}
