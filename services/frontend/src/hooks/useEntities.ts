import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/entities'
import type { EntityDomain } from '../domains'

const TAGS_PAGE_SIZE = 50
const ENTITIES_PAGE_SIZE = 50
const SEARCH_RESULT_LIMIT = 20

export function useEntities(
  params?: { domain?: EntityDomain; status?: string; include_archived?: boolean; tag?: string },
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey: ['entities', 'list', params ?? {}],
    queryFn: ({ pageParam }) =>
      api.listEntities({ ...params, limit: ENTITIES_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * ENTITIES_PAGE_SIZE : undefined,
    enabled: options?.enabled,
  })
}

// Backs the two type-ahead dropdowns (SearchBar, EntityCombobox) — a capped
// page with a "refine your search" hint, not infinite-scroll pagination
// (an unusual pattern inside a transient dropdown).
export function useEntitySearch(
  params: { search: string; domain?: EntityDomain },
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ['entities', 'search', params],
    queryFn: () => api.listEntities({ ...params, limit: SEARCH_RESULT_LIMIT }),
    enabled: options?.enabled,
  })
}

export function useEntityTags(
  params: { q: string; domain?: EntityDomain; include_archived?: boolean },
  options?: { enabled?: boolean },
) {
  return useInfiniteQuery({
    queryKey: ['entities', 'tags', params.q, params.domain ?? null, params.include_archived ?? false],
    queryFn: ({ pageParam }) =>
      api.listEntityTags({
        q: params.q || undefined,
        domain: params.domain,
        include_archived: params.include_archived,
        limit: TAGS_PAGE_SIZE,
        offset: pageParam,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * TAGS_PAGE_SIZE : undefined,
    enabled: options?.enabled,
  })
}

export function useEntity(id: string | undefined) {
  return useQuery({
    queryKey: ['entities', 'detail', id],
    queryFn: () => api.getEntity(id as string),
    enabled: id !== undefined,
  })
}

export function useCreateEntity() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.createEntity,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useUpdateEntity(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: api.EntityUpdateInput) => api.updateEntity(id, input),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useArchiveEntity() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.archiveEntity,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useRestoreEntity() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.restoreEntity,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useBulkArchiveEntities() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.bulkArchiveEntities,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useBulkRestoreEntities() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.bulkRestoreEntities,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useDeleteEntity() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteEntity,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}

export function useTrashedEntities() {
  return useQuery({
    queryKey: ['entities', 'trash'],
    queryFn: api.listTrashedEntities,
  })
}

export function useRestoreEntityFromTrash() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.restoreEntityFromTrash,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['entities'] }),
  })
}
