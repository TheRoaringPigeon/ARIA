import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { DOMAIN_REGISTRY } from '../domains'
import { useEntities } from '../hooks/useEntities'

const MIN_QUERY_LENGTH = 2
const DEBOUNCE_MS = 300

export function SearchBar() {
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

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
  const results = useEntities({ search: debouncedQuery }, { enabled })

  function goTo(entityId: string) {
    navigate(`/entities/${entityId}`)
    setQuery('')
    setDebouncedQuery('')
    setOpen(false)
  }

  return (
    <div ref={containerRef} className="relative w-full max-w-xs">
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
          if (e.key === 'Enter' && results.data?.length) goTo(results.data[0].id)
        }}
        placeholder="Search entities…"
        className="w-full rounded-md border border-line bg-transparent px-3 py-1.5 text-sm"
      />
      {open && enabled && (
        <div className="absolute top-full left-0 right-0 mt-1 rounded-md border border-divider bg-surface shadow-lg z-10 max-h-80 overflow-y-auto">
          {results.isPending && <p className="p-3 text-sm text-subtle">Searching…</p>}
          {results.isError && <p className="p-3 text-sm text-red-500">Search failed.</p>}
          {results.data?.length === 0 && <p className="p-3 text-sm text-subtle">No matching entities.</p>}
          {results.data?.map((entity) => (
            <button
              key={entity.id}
              type="button"
              onClick={() => goTo(entity.id)}
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
