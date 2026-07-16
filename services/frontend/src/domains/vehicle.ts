import type { DomainConfig, FieldConfig } from './base'
import type { GeneratedVehicleAttrs } from './generated'
import { GENERATED } from './generated'

export type VehicleAttrs = GeneratedVehicleAttrs

const FIELDS: FieldConfig<VehicleAttrs>[] = [
  { key: 'make', label: 'Make', kind: 'text', required: true },
  { key: 'model', label: 'Model', kind: 'text', required: true },
  { key: 'year', label: 'Year', kind: 'number', required: true },
  { key: 'vin', label: 'VIN', kind: 'text' },
  { key: 'license_plate', label: 'License plate', kind: 'text' },
  { key: 'current_mileage', label: 'Current mileage', kind: 'number' },
  { key: 'purchase_date', label: 'Purchase date', kind: 'date' },
]

export const vehicleConfig: DomainConfig<VehicleAttrs, 'vehicle'> = {
  label: 'Vehicle',
  statuses: GENERATED.vehicle.statuses,
  logTypes: GENERATED.vehicle.logTypes,
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'vehicle', make: '', model: '', year: new Date().getFullYear() }),
  namePlaceholder: '2021 Ford Ranger',
  locationPlaceholder: 'Garage, kitchen, ...',
  uiVariant: 'schedule',
}
