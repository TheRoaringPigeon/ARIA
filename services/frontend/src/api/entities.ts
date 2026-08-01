import { apiDelete, apiGet, apiPatch, apiPost, CORE_API_URL } from './client'
import type { Entity, SharedWith } from './types'
import type { EntityAttributes, EntityDomain } from '../domains'

export interface EntityCreateInput {
  domain: EntityDomain
  name: string
  status: string
  tags?: string[]
  location?: string | null
  specs?: Record<string, string>
  shared_with?: SharedWith
  attributes: EntityAttributes
}

export interface EntityUpdateInput {
  name?: string
  status?: string
  tags?: string[]
  location?: string | null
  specs?: Record<string, string>
  shared_with?: SharedWith
  attributes?: EntityAttributes
}

export function listEntities(params?: {
  domain?: EntityDomain
  include_archived?: boolean
  search?: string
  tag?: string
}): Promise<Entity[]> {
  const search = new URLSearchParams()
  if (params?.domain) search.set('domain', params.domain)
  if (params?.include_archived) search.set('include_archived', 'true')
  if (params?.search) search.set('q', params.search)
  if (params?.tag) search.set('tag', params.tag)
  const qs = search.toString()
  return apiGet<Entity[]>(`/entities${qs ? `?${qs}` : ''}`)
}

export interface EntityTagsPage {
  tags: string[]
  has_more: boolean
}

export function listEntityTags(params?: {
  q?: string
  domain?: EntityDomain
  include_archived?: boolean
  limit?: number
  offset?: number
}): Promise<EntityTagsPage> {
  const search = new URLSearchParams()
  if (params?.q) search.set('q', params.q)
  if (params?.domain) search.set('domain', params.domain)
  if (params?.include_archived) search.set('include_archived', 'true')
  if (params?.limit !== undefined) search.set('limit', String(params.limit))
  if (params?.offset !== undefined) search.set('offset', String(params.offset))
  const qs = search.toString()
  return apiGet<EntityTagsPage>(`/entities/tags${qs ? `?${qs}` : ''}`)
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

export interface BulkEntityResult {
  succeeded: string[]
  not_found: string[]
  forbidden: string[]
}

export function bulkArchiveEntities(ids: string[]): Promise<BulkEntityResult> {
  return apiPost<BulkEntityResult>('/entities/bulk-archive', { ids })
}

export function bulkRestoreEntities(ids: string[]): Promise<BulkEntityResult> {
  return apiPost<BulkEntityResult>('/entities/bulk-restore', { ids })
}

export function deleteEntity(id: string): Promise<void> {
  return apiDelete(`/entities/${id}`)
}

// Moves the entity to trash server-side (see deleteEntity) rather than
// deleting it outright — this listing is the "Recently Deleted" view of
// what's currently in that grace period.
export function listTrashedEntities(): Promise<Entity[]> {
  return apiGet<Entity[]>('/entities/trash')
}

export function restoreEntityFromTrash(id: string): Promise<Entity> {
  return apiPost<Entity>(`/entities/${id}/restore-from-trash`)
}

// A plain URL for a browser-native <a href> download, not fetched through
// apiFetch — same convention as documents.ts's downloadUrl. The browser
// handles the byte stream and Content-Disposition itself, and cookies still
// ride along via the browser's normal same-site request flow.
export function exportUrl(id: string, opts?: { includeDocuments?: boolean }): string {
  const qs = opts?.includeDocuments ? '?include_documents=true' : ''
  return `${CORE_API_URL}/entities/${id}/export.pdf${qs}`
}
