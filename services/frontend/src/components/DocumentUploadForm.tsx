import { useState, type FormEvent } from 'react'
import type { DocumentType, SharedWith } from '../api/types'
import { SharingControl } from './SharingControl'

const DOCUMENT_TYPES: DocumentType[] = ['manual', 'receipt', 'invoice', 'photo', 'diagram', 'other']

interface Props {
  onSubmit: (input: { file: File; documentType: DocumentType; sharedWith: SharedWith }) => void
  isSubmitting?: boolean
  submitError?: string | null
}

export function DocumentUploadForm({ onSubmit, isSubmitting, submitError }: Props) {
  const [file, setFile] = useState<File | null>(null)
  const [documentType, setDocumentType] = useState<DocumentType>('manual')
  const [sharedWith, setSharedWith] = useState<SharedWith>('household')

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    onSubmit({ file, documentType, sharedWith })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-sm">File</span>
          <input
            type="file"
            required
            accept="application/pdf,image/jpeg,image/png"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
          />
        </label>
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
      </div>

      <SharingControl value={sharedWith} onChange={setSharedWith} />

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting || !file}
        className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Uploading…' : 'Upload'}
      </button>
    </form>
  )
}
