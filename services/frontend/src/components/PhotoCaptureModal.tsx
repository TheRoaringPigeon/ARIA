import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { draftPageUrl } from '../api/documentDrafts'
import type { DocumentType, SharedWith } from '../api/types'
import { useDocumentDraft, useDocumentDraftQuery } from '../hooks/useDocumentDraft'
import { SharingControl } from './SharingControl'

const DOCUMENT_TYPES: DocumentType[] = ['manual', 'receipt', 'invoice', 'photo', 'diagram', 'other']

interface PendingShot {
  file: File
  status: 'uploading' | 'error'
  error: string | null
}

interface Props {
  entityId: string
  onClose: () => void
}

export function PhotoCaptureModal({ entityId, onClose }: Props) {
  const storageKey = `documentDraftId:${entityId}`
  const queryClient = useQueryClient()

  const [draftId, setDraftId] = useState<string | null>(null)
  const [documentType, setDocumentType] = useState<DocumentType>('manual')
  const [sharedWith, setSharedWith] = useState<SharedWith>('household')
  const [pendingShot, setPendingShot] = useState<PendingShot | null>(null)
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const finalizeHandledRef = useRef(false)

  // Resume-from-refresh: this modal's lifetime is one capture session, so
  // this only needs to run once on mount, not on every storageKey render.
  useEffect(() => {
    const stored = localStorage.getItem(storageKey)
    if (stored) setDraftId(stored)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const draftQuery = useDocumentDraftQuery(draftId ?? undefined)
  const { createDraft, uploadPage, deletePage, reorderPages, finalizeDraft, cancelDraft } =
    useDocumentDraft(draftId ?? undefined)

  const draft = draftQuery.data

  // The stored draft id no longer resolves (already finalized elsewhere,
  // or expired) — drop it and fall back to the start-a-new-capture view.
  useEffect(() => {
    if (draftId && draftQuery.isError) {
      localStorage.removeItem(storageKey)
      setDraftId(null)
    }
  }, [draftId, draftQuery.isError, storageKey])

  // finalizeDraft flips status to `finalizing`; once the worker finishes it
  // becomes `finalized` here (possibly after a resume + poll, not only
  // right after this tab's own "Create" tap) — hand off to the entity's
  // document list and close.
  useEffect(() => {
    if (draft?.status === 'finalized' && !finalizeHandledRef.current) {
      finalizeHandledRef.current = true
      queryClient.invalidateQueries({ queryKey: ['documents', entityId] })
      localStorage.removeItem(storageKey)
      cancelDraft.mutate(undefined, { onSettled: onClose })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.status])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  function handleStart(e: FormEvent) {
    e.preventDefault()
    createDraft.mutate(
      { entityId, documentType, sharedWith },
      {
        onSuccess: (created) => {
          localStorage.setItem(storageKey, created.id)
          setDraftId(created.id)
        },
      },
    )
  }

  function uploadShot(file: File) {
    setPendingShot({ file, status: 'uploading', error: null })
    uploadPage.mutate(
      { file },
      {
        onSuccess: () => setPendingShot(null),
        onError: (err) =>
          setPendingShot({
            file,
            status: 'error',
            error: err instanceof ApiError ? err.message : 'Upload failed',
          }),
      },
    )
  }

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    // Reset so choosing the same file again (e.g. right after a Discard)
    // still fires a change event.
    e.target.value = ''
    if (file) uploadShot(file)
  }

  function movePage(pageId: string, direction: -1 | 1) {
    if (!draft) return
    const ids = draft.pages.map((p) => p.id)
    const index = ids.indexOf(pageId)
    const swapWith = index + direction
    if (swapWith < 0 || swapWith >= ids.length) return
    ;[ids[index], ids[swapWith]] = [ids[swapWith], ids[index]]
    reorderPages.mutate(ids)
  }

  function handleCancel() {
    if (!draftId) {
      onClose()
      return
    }
    localStorage.removeItem(storageKey)
    cancelDraft.mutate(undefined, { onSettled: onClose })
  }

  const shutterDisabled = pendingShot !== null || draft?.status !== 'capturing' || uploadPage.isPending

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-lg border border-divider bg-surface shadow-lg p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-sm font-medium">Take photos</p>

        {!draftId && (
          <form onSubmit={handleStart} className="mt-3 space-y-3">
            <label className="block">
              <span className="text-sm">Type</span>
              <select
                value={documentType}
                onChange={(e) => setDocumentType(e.target.value as DocumentType)}
                className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
              >
                {DOCUMENT_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>

            <SharingControl value={sharedWith} onChange={setSharedWith} />

            {createDraft.error && (
              <p className="text-sm text-red-500">
                {createDraft.error instanceof ApiError
                  ? createDraft.error.message
                  : 'Could not start capture.'}
              </p>
            )}

            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-md border border-line px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={createDraft.isPending}
                className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium disabled:opacity-50"
              >
                {createDraft.isPending ? 'Starting…' : 'Start'}
              </button>
            </div>
          </form>
        )}

        {draftId && draftQuery.isPending && <p className="mt-3 text-subtle">Loading…</p>}

        {draft?.status === 'capturing' && (
          <div className="mt-3">
            <div className="flex flex-wrap gap-2">
              {draft.pages.map((page, index) => (
                <div key={page.id} className="flex flex-col items-center gap-1">
                  <button
                    type="button"
                    onClick={() => setSelectedPageId((cur) => (cur === page.id ? null : page.id))}
                    className={`h-20 w-20 overflow-hidden rounded-md border-2 ${
                      selectedPageId === page.id ? 'border-primary' : 'border-line'
                    }`}
                  >
                    <img
                      src={draftPageUrl(draft.id, page.id)}
                      alt={`Page ${index + 1}`}
                      className="h-full w-full object-cover"
                    />
                  </button>
                  <div className="flex items-center gap-1">
                    {selectedPageId === page.id && (
                      <>
                        <button
                          type="button"
                          onClick={() => movePage(page.id, -1)}
                          disabled={index === 0}
                          aria-label="Move earlier"
                          className="rounded border border-line px-1.5 text-xs disabled:opacity-30"
                        >
                          ‹
                        </button>
                        <button
                          type="button"
                          onClick={() => movePage(page.id, 1)}
                          disabled={index === draft.pages.length - 1}
                          aria-label="Move later"
                          className="rounded border border-line px-1.5 text-xs disabled:opacity-30"
                        >
                          ›
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      onClick={() => deletePage.mutate(page.id)}
                      aria-label="Delete photo"
                      className="rounded border border-line px-1.5 text-xs text-red-500"
                    >
                      ×
                    </button>
                  </div>
                </div>
              ))}

              {pendingShot && (
                <div className="flex h-20 w-20 flex-col items-center justify-center gap-1 rounded-md border-2 border-line p-1 text-center">
                  {pendingShot.status === 'uploading' ? (
                    <span className="text-xs text-subtle">Uploading…</span>
                  ) : (
                    <>
                      <span className="text-[10px] leading-tight text-red-500">{pendingShot.error}</span>
                      <div className="flex gap-1">
                        <button
                          type="button"
                          onClick={() => uploadShot(pendingShot.file)}
                          className="rounded border border-line px-1 text-[10px]"
                        >
                          Retry
                        </button>
                        <button
                          type="button"
                          onClick={() => setPendingShot(null)}
                          className="rounded border border-line px-1 text-[10px]"
                        >
                          Discard
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              onChange={handleFileChange}
              className="hidden"
            />
            <div className="mt-3 flex justify-center">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={shutterDisabled}
                className="rounded-md bg-primary text-white hover:bg-primary-hover px-6 py-3 text-sm font-medium disabled:opacity-50"
              >
                Take photo
              </button>
            </div>

            {finalizeDraft.error && (
              <p className="mt-2 text-sm text-red-500">
                {finalizeDraft.error instanceof ApiError
                  ? finalizeDraft.error.message
                  : 'Could not start creating the document.'}
              </p>
            )}

            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={handleCancel}
                className="rounded-md border border-line px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => finalizeDraft.mutate()}
                disabled={draft.pages.length === 0 || finalizeDraft.isPending || pendingShot !== null}
                className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium disabled:opacity-50"
              >
                Create
              </button>
            </div>
          </div>
        )}

        {draft?.status === 'finalizing' && (
          <p className="mt-3 text-subtle">Creating…</p>
        )}

        {draft?.status === 'finalized' && <p className="mt-3 text-subtle">Done.</p>}

        {draft?.status === 'failed' && (
          <div className="mt-3">
            <p className="text-sm text-red-500">
              {draft.finalize_error ?? 'Could not create the document.'}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={handleCancel}
                className="rounded-md border border-line px-3 py-1.5 text-sm"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => finalizeDraft.mutate()}
                disabled={finalizeDraft.isPending}
                className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium disabled:opacity-50"
              >
                Retry
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
