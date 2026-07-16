import { useMutation, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/documents'
import type { DocumentType } from '../api/types'

export function useUploadDocument(entityId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ file, documentType }: { file: File; documentType: DocumentType }) =>
      api.uploadDocument(entityId, file, documentType),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['documents', entityId] }),
  })
}
