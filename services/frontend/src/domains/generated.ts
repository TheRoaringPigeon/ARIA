// GENERATED FILE — do not edit by hand.
// Source of truth: libs/shared/src/aria_shared/models/entities/*.py
// Regenerate (from repo root):
//   uv run --package aria-shared python -m aria_shared.models.entities.export_ts \
//     --out services/frontend/src/domains/generated.ts
// Drift is caught by libs/shared/tests/test_export_ts.py (pytest).

export const ENTITY_DOMAINS = ['home', 'vehicle', 'equipment', 'project', 'person'] as const
export type GeneratedEntityDomain = (typeof ENTITY_DOMAINS)[number]

export interface GeneratedHomeAttrs {
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

export interface GeneratedVehicleAttrs {
  domain: 'vehicle'
  make: string
  model: string
  year: number
  vin?: string | null
  license_plate?: string | null
  current_mileage?: number | null
  purchase_date?: string | null
}

export interface GeneratedEquipmentAttrs {
  domain: 'equipment'
  make?: string | null
  model?: string | null
  serial_number?: string | null
  purchase_date?: string | null
}

export interface GeneratedProjectAttrs {
  domain: 'project'
  related_entity_ids: string[]
  start_date?: string | null
  target_end_date?: string | null
  completed_date?: string | null
  budget_estimate?: number | null
}

export interface GeneratedPersonAttrs {
  domain: 'person'
  relationship?: string | null
  company?: string | null
  job_title?: string | null
  email?: string | null
  phone?: string | null
  birthday?: string | null
}

export const GENERATED = {
  home: {
    statuses: ['active', 'needs_attention', 'archived'] as const,
    logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'] as const,
    literalOptions: {
      entity_type: ['room', 'system', 'appliance', 'structure'] as const,
    },
  },
  vehicle: {
    statuses: ['active', 'in_service', 'sold', 'archived'] as const,
    logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'] as const,
    literalOptions: {},
  },
  equipment: {
    statuses: ['active', 'in_service', 'retired'] as const,
    logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'] as const,
    literalOptions: {},
  },
  project: {
    statuses: ['planning', 'in_progress', 'on_hold', 'completed'] as const,
    logTypes: ['service', 'repair', 'inspection', 'expense', 'note', 'milestone'] as const,
    literalOptions: {},
  },
  person: {
    statuses: ['active', 'inactive'] as const,
    logTypes: ['conversation', 'call', 'meeting', 'gift', 'milestone'] as const,
    literalOptions: {},
  },
} as const

export const LOG_TYPES = ['service', 'repair', 'inspection', 'expense', 'note', 'milestone', 'conversation', 'call', 'meeting', 'gift'] as const
export type LogType = (typeof LOG_TYPES)[number]
export type LogTypeFor<D extends GeneratedEntityDomain> = (typeof GENERATED)[D]['logTypes'][number]
