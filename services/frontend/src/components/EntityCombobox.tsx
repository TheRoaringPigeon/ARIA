import { useEffect, useRef, useState } from 'react'
import type { Entity } from '../api/types'
import { DOMAIN_REGISTRY, type EntityDomain } from '../domains'
import { useEntities } from '../hooks/useEntities'

const MIN_QUERY_LENGTH = 2
const DEBOUNCE_MS = 300

interface Props {
  domain?: EntityDomain
  onSelect: (entity: Entity) => void
  placeholder?: string
}

// Same debounce/open/click-outside/useEntities shape as SearchBar.tsx, but
// this calls back with the selected entity instead of navigating — SearchBar
// itself is left untouched, this is a focused sibling for pickers that need
// a value rather than a page transition.
export function EntityCombobox({ domain, onSelect, placeholder }: Props) {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [query])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const enabled = debouncedQuery.length >= MIN_QUERY_LENGTH
  const results = useEntities({ search: debouncedQuery, domain }, { enabled })

  function pick(entity: Entity) {
    onSelect(entity)
    setQuery('')
    setDebouncedQuery('')
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative w-full">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false)
          const resultsAreFresh = debouncedQuery === query.trim()
          if (e.key === 'Enter' && open && resultsAreFresh && results.data?.length) {
            e.preventDefault()
            pick(results.data[0])
          }
        }}
        placeholder={placeholder ?? 'Search entities…'}
        className="w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
      />
      {open && enabled && (
        <div className="absolute top-full left-0 right-0 mt-1 rounded-md border border-divider bg-surface shadow-lg z-30 max-h-60 overflow-y-auto">
          {results.isPending && <p className="p-3 text-sm text-subtle">Searching…</p>}
          {results.isError && <p className="p-3 text-sm text-red-500">Search failed.</p>}
          {results.data?.length === 0 && <p className="p-3 text-sm text-subtle">No matching entities.</p>}
          {results.data?.map((entity) => (
            <button
              key={entity.id}
              type="button"
              onClick={() => pick(entity)}
              className="w-full text-left px-3 py-2 text-sm hover:bg-surface-hover flex items-center justify-between"
            >
              <span className="font-medium">{entity.name}</span>
              <span className="text-xs text-subtle">{DOMAIN_REGISTRY[entity.domain].label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
