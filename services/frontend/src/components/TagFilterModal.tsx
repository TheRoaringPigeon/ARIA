import { useEffect, useRef, useState } from 'react'
import type { EntityDomain } from '../domains'
import { useEntityTags } from '../hooks/useEntities'

const DEBOUNCE_MS = 300

interface TagFilterModalProps {
  value: string
  onChange: (tag: string) => void
  onClose: () => void
  domain?: EntityDomain
  includeArchived: boolean
}

export function TagFilterModal({ value, onChange, onClose, domain, includeArchived }: TagFilterModalProps) {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [query])

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  const tagsQuery = useEntityTags({ q: debouncedQuery, domain, include_archived: includeArchived })
  const tags = tagsQuery.data?.pages.flatMap((page) => page.tags) ?? []

  function select(tag: string) {
    onChange(tag)
    onClose()
  }

  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center bg-black/40 pt-24" onClick={onClose}>
      <div
        className="w-full max-w-sm rounded-lg border border-divider bg-surface shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-3 border-b border-divider">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tags…"
            className="w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
          />
        </div>
        <div className="max-h-80 overflow-y-auto">
          <button
            type="button"
            onClick={() => select('')}
            className={`w-full text-left px-3 py-2 text-sm hover:bg-surface-hover ${
              value === '' ? 'bg-active' : ''
            }`}
          >
            All tags
          </button>
          {tagsQuery.isPending && <p className="px-3 py-2 text-sm text-subtle">Loading…</p>}
          {tagsQuery.isError && <p className="px-3 py-2 text-sm text-red-500">Failed to load tags.</p>}
          {tagsQuery.isSuccess && tags.length === 0 && (
            <p className="px-3 py-2 text-sm text-subtle">No matching tags.</p>
          )}
          {tags.map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => select(tag)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-surface-hover ${
                value === tag ? 'bg-active' : ''
              }`}
            >
              {tag}
            </button>
          ))}
          {tagsQuery.hasNextPage && (
            <button
              type="button"
              onClick={() => tagsQuery.fetchNextPage()}
              disabled={tagsQuery.isFetchingNextPage}
              className="w-full px-3 py-2 text-sm text-subtle hover:bg-surface-hover disabled:opacity-50"
            >
              {tagsQuery.isFetchingNextPage ? 'Loading…' : 'Load more'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
