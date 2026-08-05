import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import * as api from '../api/documentDrafts'
import type { DocumentDraft, DocumentType, SharedWith } from '../api/types'

function draftQueryKey(draftId: string | undefined) {
  return ['documentDraft', draftId] as const
}

// Polls while a finalize is in flight so a refresh mid-finalize (or a tab
// left open after tapping "Create") keeps watching for the worker to
// finish, instead of leaving the modal on a stale `finalizing` snapshot.
export function useDocumentDraftQuery(draftId: string | undefined) {
  return useQuery({
    queryKey: draftQueryKey(draftId),
    queryFn: () => api.getDraft(draftId as string),
    enabled: draftId !== undefined,
    retry: false,
    refetchInterval: (query) => {
      const draft = query.state.data as DocumentDraft | undefined
      return draft?.status === 'finalizing' ? 1500 : false
    },
  })
}

export function useDocumentDraft(draftId: string | undefined) {
  const queryClient = useQueryClient()

  // Every draft-mutating endpoint returns the updated draft, so mutations
  // write it straight into the query cache instead of triggering a refetch
  // round trip — the modal's thumbnail strip updates from the same
  // response it just got back from the upload/delete/reorder call.
  function setDraft(draft: DocumentDraft) {
    queryClient.setQueryData(draftQueryKey(draft.id), draft)
  }

  const createDraft = useMutation({
    mutationFn: ({
      entityId,
      documentType,
      sharedWith,
    }: {
      entityId: string
      documentType: DocumentType
      sharedWith: SharedWith
    }) => api.createDraft(entityId, documentType, sharedWith),
    onSuccess: setDraft,
  })

  const uploadPage = useMutation({
    mutationFn: ({ file }: { file: File }) => api.uploadDraftPage(draftId as string, file),
    onSuccess: setDraft,
  })

  const deletePage = useMutation({
    mutationFn: (pageId: string) => api.deleteDraftPage(draftId as string, pageId),
    onSuccess: setDraft,
  })

  const reorderPages = useMutation({
    mutationFn: (pageIds: string[]) => api.reorderDraftPages(draftId as string, pageIds),
    onSuccess: setDraft,
    onError: () => {
      // A 409 means another tab changed the pages concurrently — refetch
      // to get the real current order rather than leaving a stale one on
      // screen, and drop the pending reorder (no retry of the same call).
      queryClient.invalidateQueries({ queryKey: draftQueryKey(draftId) })
    },
  })

  const finalizeDraft = useMutation({
    mutationFn: () => api.finalizeDraft(draftId as string),
    onSuccess: setDraft,
  })

  const cancelDraft = useMutation({
    mutationFn: () => api.cancelDraft(draftId as string),
  })

  return { createDraft, uploadPage, deletePage, reorderPages, finalizeDraft, cancelDraft }
}
