import { useInfiniteQuery } from '@tanstack/react-query'
import * as api from '../api/documents'
import type { DocumentsPage } from '../api/documents'
import type { Document } from '../api/types'
import { dedupeInfinitePages } from '../lib/pagination'

const IN_PROGRESS_STATUSES = new Set<Document['processing_status']>([
  'pending',
  'ocr_complete',
  'chunked',
])

const DOCUMENTS_PAGE_SIZE = 50

export function useEntityDocuments(entityId: string | undefined) {
  return useInfiniteQuery({
    queryKey: ['documents', entityId],
    queryFn: ({ pageParam }) =>
      api.listEntityDocuments(entityId as string, { limit: DOCUMENTS_PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.has_more ? allPages.length * DOCUMENTS_PAGE_SIZE : undefined,
    enabled: entityId !== undefined,
    select: dedupeInfinitePages,
    // A document mid-OCR could be on any loaded page, not just the first —
    // check every page currently in the cache, not just the latest one.
    refetchInterval: (query) => {
      const pages = query.state.data?.pages as DocumentsPage[] | undefined
      const stillProcessing = pages?.some((page) =>
        page.items.some((doc) => IN_PROGRESS_STATUSES.has(doc.processing_status)),
      )
      return stillProcessing ? 2000 : false
    },
  })
}
