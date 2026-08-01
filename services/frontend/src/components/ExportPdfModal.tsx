import { useEffect, useState } from 'react'
import { exportUrl } from '../api/entities'

interface ExportPdfModalProps {
  entityId: string
  documentCount: number
  onClose: () => void
}

export function ExportPdfModal({ entityId, documentCount, onClose }: ExportPdfModalProps) {
  // Unchecked by default — matches this app's existing opt-in-extras
  // convention (e.g. notify_overdue_email defaults off); attaching originals
  // is real extra fetch/merge work server-side, not something to do
  // unasked.
  const [includeDocuments, setIncludeDocuments] = useState(false)

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-20 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-divider bg-surface shadow-lg p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <p className="text-sm font-medium">Export PDF</p>
        <label className="mt-3 flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5"
            checked={includeDocuments}
            onChange={(e) => setIncludeDocuments(e.target.checked)}
          />
          <span>
            Include {documentCount} linked document{documentCount === 1 ? '' : 's'} in the PDF
            <span className="block text-subtle text-xs">
              Images are embedded; PDF documents are appended as extra pages.
            </span>
          </span>
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-line px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
          <a
            href={exportUrl(entityId, { includeDocuments })}
            onClick={onClose}
            className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
          >
            Download PDF
          </a>
        </div>
      </div>
    </div>
  )
}
