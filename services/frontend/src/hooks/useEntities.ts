import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/entities'
import type { EntityDomain } from '../api/types'

export function useEntities(params?: { domain?: EntityDomain; include_archived?: boolean }) {
  return useQuery({
    queryKey: ['entities', 'list', params ?? {}],
    queryFn: () => api.listEntities(params),
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
