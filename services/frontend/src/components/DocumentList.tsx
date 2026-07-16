import { downloadUrl } from '../api/documents'
import type { Document, ProcessingStatus } from '../api/types'

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
}

export function DocumentList({ documents, onDelete }: Props) {
  if (documents.length === 0) {
    return <p className="text-subtle">No documents yet.</p>
  }

  return (
    <div className="grid gap-2">
      {documents.map((doc) => (
        <div key={doc.id} className="rounded-lg border border-divider p-3">
          <div className="flex items-start justify-between gap-3">
            <p className="font-medium">{doc.original_filename}</p>
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
                onClick={() => {
                  if (window.confirm('Delete this document? This cannot be undone.')) {
                    onDelete(doc.id)
                  }
                }}
                className="text-sm text-red-500 hover:underline"
              >
                Delete
              </button>
            </div>
          </div>
          <p className="text-sm text-subtle">
            {doc.document_type} · {doc.uploaded_at.slice(0, 10)}
          </p>
          {doc.processing_status === 'failed' && doc.processing_error && (
            <p className="mt-1 text-sm text-red-500">{doc.processing_error}</p>
          )}
        </div>
      ))}
    </div>
  )
}
