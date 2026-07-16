import { equipmentConfig, type EquipmentAttrs } from './equipment'
import { homeConfig, type HomeAttrs } from './home'
import { personConfig, type PersonAttrs } from './person'
import { projectConfig, type ProjectAttrs } from './project'
import { vehicleConfig, type VehicleAttrs } from './vehicle'

export type { DomainConfig, FieldConfig } from './base'

// Adding a domain: create <domain>.ts (interface + fields + config object),
// then add it to DOMAIN_REGISTRY and the EntityAttributes union below.
export const DOMAIN_REGISTRY = {
  home: homeConfig,
  vehicle: vehicleConfig,
  equipment: equipmentConfig,
  project: projectConfig,
  person: personConfig,
} as const

export type EntityDomain = keyof typeof DOMAIN_REGISTRY
export type EntityAttributes = HomeAttrs | VehicleAttrs | EquipmentAttrs | ProjectAttrs | PersonAttrs
export const DOMAINS = Object.keys(DOMAIN_REGISTRY) as EntityDomain[]

export type { HomeAttrs, VehicleAttrs, EquipmentAttrs, ProjectAttrs, PersonAttrs }
