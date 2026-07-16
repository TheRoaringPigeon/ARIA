import { apiGet, apiPatch, apiPost } from './client'
import type { Entity } from './types'
import type { EntityAttributes, EntityDomain } from '../domains'

export interface EntityCreateInput {
  domain: EntityDomain
  name: string
  status: string
  tags?: string[]
  location?: string | null
  specs?: Record<string, string>
  attributes: EntityAttributes
}

export interface EntityUpdateInput {
  name?: string
  status?: string
  tags?: string[]
  location?: string | null
  specs?: Record<string, string>
  attributes?: EntityAttributes
}

export function listEntities(params?: {
  domain?: EntityDomain
  include_archived?: boolean
}): Promise<Entity[]> {
  const search = new URLSearchParams()
  if (params?.domain) search.set('domain', params.domain)
  if (params?.include_archived) search.set('include_archived', 'true')
  const qs = search.toString()
  return apiGet<Entity[]>(`/entities${qs ? `?${qs}` : ''}`)
}

export function getEntity(id: string): Promise<Entity> {
  return apiGet<Entity>(`/entities/${id}`)
}

export function createEntity(input: EntityCreateInput): Promise<Entity> {
  return apiPost<Entity>('/entities', input)
}

export function updateEntity(id: string, input: EntityUpdateInput): Promise<Entity> {
  return apiPatch<Entity>(`/entities/${id}`, input)
}

export function archiveEntity(id: string): Promise<Entity> {
  return apiPost<Entity>(`/entities/${id}/archive`)
}

export function restoreEntity(id: string): Promise<Entity> {
  return apiPost<Entity>(`/entities/${id}/restore`)
}
