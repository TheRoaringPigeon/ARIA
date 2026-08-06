import { useRef, useState } from 'react'
import { downloadUrl } from '../api/documents'
import type { Document, ProcessingStatus } from '../api/types'
import { ConfirmDialog } from './ConfirmDialog'
import { SharedWithLabel } from './SharingControl'

const STATUS_COLOR: Record<ProcessingStatus, string> = {
  pending: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  ocr_complete: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  chunked: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  embedded: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300',
}

function ProcessingStatusBadge({ status }: { status: ProcessingStatus }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_COLOR[status]}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

interface Props {
  documents: Document[]
  onDelete: (id: string) => void
  onRename: (id: string, name: string) => void
}

export function DocumentList({ documents, onDelete, onRename }: Props) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const committedRef = useRef(false)

  if (documents.length === 0) {
    return <p className="text-subtle">No documents yet.</p>
  }

  function startRename(doc: Document) {
    committedRef.current = false
    setRenamingId(doc.id)
    setRenameValue(doc.original_filename)
  }

  function commitRename(id: string) {
    // Guards against a double commit: submitting the form fires a blur
    // event on the input's removal, which would otherwise call this a
    // second time with the same value. A ref (not renamingId state) is used
    // because both the submit and blur handlers close over the same render
    // and would otherwise both see the pre-commit value.
    if (committedRef.current) return
    committedRef.current = true
    const trimmed = renameValue.trim()
    if (trimmed) onRename(id, trimmed)
    setRenamingId(null)
  }

  return (
    <div className="grid gap-2">
      {documents.map((doc) => (
        <div key={doc.id} className="rounded-lg border border-divider p-3">
          <div className="flex items-start justify-between gap-3">
            {renamingId === doc.id ? (
              <form
                className="flex flex-1 items-center gap-1"
                onSubmit={(e) => {
                  e.preventDefault()
                  commitRename(doc.id)
                }}
              >
                <input
                  type="text"
                  autoFocus
                  value={renameValue}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onBlur={() => commitRename(doc.id)}
                  maxLength={200}
                  className="w-full rounded-md border border-line bg-transparent px-2 py-1 text-sm font-medium"
                />
              </form>
            ) : (
              <p className="font-medium">{doc.original_filename}</p>
            )}
            <div className="flex items-center gap-2 shrink-0">
              <ProcessingStatusBadge status={doc.processing_status} />
              <a
                href={downloadUrl(doc.id)}
                className="text-sm text-subtle hover:underline"
              >
                Download
              </a>
              <button
                type="button"
                onClick={() => startRename(doc)}
                className="text-sm text-subtle hover:underline"
              >
                Rename
              </button>
              <button
                type="button"
                onClick={() => setConfirmingId(doc.id)}
                className="text-sm text-red-500 hover:underline"
              >
                Delete
              </button>
            </div>
          </div>
          <p className="text-sm text-subtle">
            {doc.document_type} · {doc.uploaded_at.slice(0, 10)} ·{' '}
            <SharedWithLabel sharedWith={doc.shared_with} />
          </p>
          {doc.processing_status === 'failed' && doc.processing_error && (
            <p className="mt-1 text-sm text-red-500">{doc.processing_error}</p>
          )}
        </div>
      ))}

      {confirmingId && (
        <ConfirmDialog
          message="Delete this document? This cannot be undone."
          onConfirm={() => {
            onDelete(confirmingId)
            setConfirmingId(null)
          }}
          onCancel={() => setConfirmingId(null)}
        />
      )}
    </div>
  )
}
