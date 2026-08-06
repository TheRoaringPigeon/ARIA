import { ApiError, CORE_API_URL, apiDelete, apiGet, apiPatch, apiPost } from './client'
import type { DocumentDraft, DocumentType, SharedWith } from './types'

export function createDraft(
  entityId: string,
  documentType: DocumentType,
  sharedWith: SharedWith = 'household',
  name: string | null = null,
): Promise<DocumentDraft> {
  return apiPost<DocumentDraft>('/documents/drafts', {
    document_type: documentType,
    entity_ids: [entityId],
    shared_with: sharedWith,
    name,
  })
}

export function getDraft(draftId: string): Promise<DocumentDraft> {
  return apiGet<DocumentDraft>(`/documents/drafts/${draftId}`)
}

// Not routed through apiFetch — same reason as uploadDocument in
// api/documents.ts: a multipart body needs the browser to set its own
// Content-Type (with the multipart boundary), not apiFetch's fixed JSON one.
export async function uploadDraftPage(draftId: string, file: File): Promise<DocumentDraft> {
  const formData = new FormData()
  formData.append('file', file)

  const res = await fetch(`${CORE_API_URL}/documents/drafts/${draftId}/pages`, {
    method: 'POST',
    credentials: 'include',
    body: formData,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      // No JSON body to extract a detail message from — fall back to statusText.
    }
    throw new ApiError(res.status, detail)
  }

  return res.json() as Promise<DocumentDraft>
}

export function deleteDraftPage(draftId: string, pageId: string): Promise<DocumentDraft> {
  return apiDelete<DocumentDraft>(`/documents/drafts/${draftId}/pages/${pageId}`)
}

export function reorderDraftPages(draftId: string, pageIds: string[]): Promise<DocumentDraft> {
  return apiPatch<DocumentDraft>(`/documents/drafts/${draftId}/pages/reorder`, { page_ids: pageIds })
}

export function finalizeDraft(draftId: string): Promise<DocumentDraft> {
  return apiPost<DocumentDraft>(`/documents/drafts/${draftId}/finalize`)
}

export function cancelDraft(draftId: string): Promise<void> {
  return apiDelete<void>(`/documents/drafts/${draftId}`)
}

// A plain URL for an <img src>, not fetched through apiFetch — the browser
// handles the byte stream itself, same as api/documents.ts's downloadUrl.
// Cookies still ride along via the browser's normal same-site request flow,
// which is why thumbnails work with no explicit credentials handling here.
export function draftPageUrl(draftId: string, pageId: string): string {
  return `${CORE_API_URL}/documents/drafts/${draftId}/pages/${pageId}/file`
}
