import { useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/documents'

export function useDeleteDocument(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', entityId] }),
  })
}
