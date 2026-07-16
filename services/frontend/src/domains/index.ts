import { equipmentConfig, type EquipmentAttrs } from './equipment'
import { homeConfig, type HomeAttrs } from './home'
import { personConfig, type PersonAttrs } from './person'
import { projectConfig, type ProjectAttrs } from './project'
import { vehicleConfig, type VehicleAttrs } from './vehicle'
import { ENTITY_DOMAINS, type GeneratedEntityDomain } from './generated'
import type { DomainConfig } from './base'

export type { DomainConfig, FieldConfig } from './base'
export type { LogType } from './generated'

// Adding a domain: create <domain>.py in libs/shared (backend), regenerate
// generated.ts, then create <domain>.ts (fields + config object) and add it
// below. DOMAIN_REGISTRY's keys are checked against the generated domain
// list, so a mismatch with the backend is a tsc error.
export const DOMAIN_REGISTRY: Record<GeneratedEntityDomain, DomainConfig<any>> = {
  home: homeConfig,
  vehicle: vehicleConfig,
  equipment: equipmentConfig,
  project: projectConfig,
  person: personConfig,
}

export type EntityDomain = GeneratedEntityDomain
export type EntityAttributes = HomeAttrs | VehicleAttrs | EquipmentAttrs | ProjectAttrs | PersonAttrs
export const DOMAINS = ENTITY_DOMAINS

export type { HomeAttrs, VehicleAttrs, EquipmentAttrs, ProjectAttrs, PersonAttrs }
