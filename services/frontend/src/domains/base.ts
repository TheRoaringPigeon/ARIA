import type { LogType } from './generated'

export type { LogType }

export interface FieldConfig<TAttrs = any> {
  key: Exclude<keyof TAttrs, 'domain'>
  label: string
  kind: 'text' | 'number' | 'date' | 'select'
  required?: boolean
  options?: readonly string[]
}

export interface DomainConfig<TAttrs = any> {
  label: string
  statuses: readonly string[]
  logTypes: readonly LogType[]
  fields: readonly FieldConfig<TAttrs>[]
  defaultAttributes: () => TAttrs
  namePlaceholder?: string
  locationPlaceholder?: string
  /** Which shape of schedule/log UI this domain uses — 'plan' for relationship-style
   * domains (e.g. person), 'schedule' for maintenance-style domains. */
  uiVariant: 'schedule' | 'plan'
}
