import type { LogType } from '../api/types'

export interface FieldConfig {
  key: string
  label: string
  kind: 'text' | 'number' | 'date' | 'select'
  required?: boolean
  options?: readonly string[]
}

export interface DomainConfig<TAttrs = any> {
  label: string
  statuses: readonly string[]
  logTypes: readonly LogType[]
  fields: readonly FieldConfig[]
  defaultAttributes: () => TAttrs
  namePlaceholder?: string
  locationPlaceholder?: string
}
