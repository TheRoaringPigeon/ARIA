import type { DomainConfig, FieldConfig } from './base'

export interface PersonAttrs {
  domain: 'person'
  relationship?: string | null
  company?: string | null
  job_title?: string | null
  email?: string | null
  phone?: string | null
  birthday?: string | null
}

const FIELDS: FieldConfig[] = [
  { key: 'relationship', label: 'Relationship', kind: 'text' },
  { key: 'company', label: 'Company', kind: 'text' },
  { key: 'job_title', label: 'Job title', kind: 'text' },
  { key: 'email', label: 'Email', kind: 'text' },
  { key: 'phone', label: 'Phone', kind: 'text' },
  { key: 'birthday', label: 'Birthday', kind: 'date' },
]

export const personConfig: DomainConfig<PersonAttrs> = {
  label: 'Person',
  statuses: ['active', 'inactive'],
  logTypes: ['conversation', 'call', 'meeting', 'gift', 'milestone'],
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'person' }),
  namePlaceholder: 'Full name',
  locationPlaceholder: 'City, neighborhood, ...',
}
