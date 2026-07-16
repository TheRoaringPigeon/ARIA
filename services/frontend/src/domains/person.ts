import type { DomainConfig, FieldConfig } from './base'
import type { GeneratedPersonAttrs } from './generated'
import { GENERATED } from './generated'

export type PersonAttrs = GeneratedPersonAttrs

const FIELDS: FieldConfig<PersonAttrs>[] = [
  { key: 'relationship', label: 'Relationship', kind: 'text' },
  { key: 'company', label: 'Company', kind: 'text' },
  { key: 'job_title', label: 'Job title', kind: 'text' },
  { key: 'email', label: 'Email', kind: 'text' },
  { key: 'phone', label: 'Phone', kind: 'text' },
  { key: 'birthday', label: 'Birthday', kind: 'date' },
]

export const personConfig: DomainConfig<PersonAttrs> = {
  label: 'Person',
  statuses: GENERATED.person.statuses,
  logTypes: GENERATED.person.logTypes,
  fields: FIELDS,
  defaultAttributes: () => ({ domain: 'person' }),
  namePlaceholder: 'Full name',
  locationPlaceholder: 'City, neighborhood, ...',
  uiVariant: 'plan',
}
