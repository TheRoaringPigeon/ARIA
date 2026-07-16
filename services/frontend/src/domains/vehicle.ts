import type { DomainConfig, FieldConfig } from './base'

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

const FIELDS: FieldConfig[] = [
  { key: 'make', label: 'Make', kind: 'text', required: true },
  { key: 'model', label: 'Model', kind: 'text', required: true },
  { key: 'year', label: 'Year', kind: 'number', required: true },
  { key: 'vin', label: 'VIN', kind: 'text' },
  { key: 'license_plate', label: 'License plate', kind: 'text' },
  { key: 'current_mileage', label: 'Current mileage', kind: 'number' },
  { key: 'purchase_date', label: 'Purchase date', kind: 'date' },
]

export const vehicleConfig: DomainConfig<VehicleAttrs> = {
  label: 'Vehicle',
  statuses: ['active', 'in_service', 'sold', 'archived'],
  logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'],
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'vehicle', make: '', model: '', year: new Date().getFullYear() }),
  namePlaceholder: '2021 Ford Ranger',
  locationPlaceholder: 'Garage, kitchen, ...',
}
