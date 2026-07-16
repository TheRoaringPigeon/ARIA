import { useQuery } from '@tanstack/react-query'
import * as api from '../api/documents'
import type { Document } from '../api/types'

const IN_PROGRESS_STATUSES = new Set<Document['processing_status']>([
  'pending',
  'ocr_complete',
  'chunked',
])

export function useEntityDocuments(entityId: string | undefined) {
  return useQuery({
    queryKey: ['documents', entityId],
    queryFn: () => api.listEntityDocuments(entityId as string),
    enabled: entityId !== undefined,
    refetchInterval: (query) => {
      const documents = query.state.data as Document[] | undefined
      const stillProcessing = documents?.some((doc) => IN_PROGRESS_STATUSES.has(doc.processing_status))
      return stillProcessing ? 2000 : false
    },
  })
}
