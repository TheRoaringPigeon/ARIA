import { useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/documents'

export function useRenameDocument(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => api.renameDocument(id, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', entityId] }),
  })
}
