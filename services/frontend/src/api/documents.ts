import { ApiError, CORE_API_URL, apiDelete, apiGet, apiPatch } from './client'
import type { Document, DocumentType, SharedWith } from './types'

export interface DocumentsPage {
  items: Document[]
  has_more: boolean
  total: number
}

export function listEntityDocuments(
  entityId: string,
  params?: { limit?: number; offset?: number },
): Promise<DocumentsPage> {
  const search = new URLSearchParams()
  if (params?.limit !== undefined) search.set('limit', String(params.limit))
  if (params?.offset !== undefined) search.set('offset', String(params.offset))
  const qs = search.toString()
  return apiGet<DocumentsPage>(`/entities/${entityId}/documents${qs ? `?${qs}` : ''}`)
}

export function getDocument(id: string): Promise<Document> {
  return apiGet<Document>(`/documents/${id}`)
}

// Not routed through apiFetch — that helper always sets a JSON
// Content-Type, but a multipart upload needs the browser to set its own
// Content-Type (with the multipart boundary) from the FormData body.
export async function uploadDocument(
  entityId: string,
  file: File,
  documentType: DocumentType,
  sharedWith: SharedWith = 'household',
): Promise<Document> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('document_type', documentType)
  formData.append('entity_ids', entityId)
  // 'household' sends no shared_with fields at all — core-api's Form field
  // defaults to [] (its own "whole household" sentinel, since a
  // multipart Form field can't default to a string and a list at once).
  if (Array.isArray(sharedWith)) {
    for (const memberId of sharedWith) {
      formData.append('shared_with', memberId)
    }
  }

  const res = await fetch(`${CORE_API_URL}/documents`, {
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

  return res.json() as Promise<Document>
}

export function deleteDocument(id: string): Promise<void> {
  return apiDelete(`/documents/${id}`)
}

export function renameDocument(id: string, name: string): Promise<Document> {
  return apiPatch<Document>(`/documents/${id}`, { original_filename: name })
}

// A plain URL for a browser-native <a href> download, not fetched through
// apiFetch — the browser handles the byte stream and Content-Disposition
// itself, and cookies still ride along via the browser's normal same-site
// request flow.
export function downloadUrl(id: string): string {
  return `${CORE_API_URL}/documents/${id}/file`
}
