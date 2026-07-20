import { useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/documents'
import type { DocumentType, SharedWith } from '../api/types'

export function useUploadDocument(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({
      file,
      documentType,
      sharedWith,
    }: {
      file: File
      documentType: DocumentType
      sharedWith: SharedWith
    }) => api.uploadDocument(entityId, file, documentType, sharedWith),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', entityId] }),
  })
}
