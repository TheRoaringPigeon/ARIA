import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { EntityForm } from '../components/EntityForm'
import { StatusBadge } from '../components/StatusBadge'
import { TagFilterModal } from '../components/TagFilterModal'
import { DOMAIN_REGISTRY, DOMAINS, type EntityDomain } from '../domains'
import {
  useBulkArchiveEntities,
  useBulkRestoreEntities,
  useCreateEntity,
  useEntities,
} from '../hooks/useEntities'

const DOMAIN_FILTERS: Array<{ label: string; value: EntityDomain | undefined }> = [
  { label: 'All', value: undefined },
  ...DOMAINS.map((d) => ({ label: DOMAIN_REGISTRY[d].label, value: d })),
]

interface BulkResult {
  action: 'archive' | 'restore'
  succeeded: number
  failed: number
}

export function EntityListPage() {
  const [domain, setDomain] = useState<EntityDomain | undefined>(undefined)
  const [showArchived, setShowArchived] = useState(false)
  const [showCreate, setShowCreate] = useState(false)
  const [status, setStatus] = useState('')
  const [tag, setTag] = useState('')
  const [tagModalOpen, setTagModalOpen] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkResult, setBulkResult] = useState<BulkResult | null>(null)
  const selectAllRef = useRef<HTMLInputElement>(null)

  const entitiesQuery = useEntities({ domain, include_archived: showArchived, tag: tag || undefined })
  const createEntity = useCreateEntity()
  const bulkArchive = useBulkArchiveEntities()
  const bulkRestore = useBulkRestoreEntities()

  function clearSelection() {
    setSelected(new Set())
    setBulkResult(null)
  }

  function handleDomainChange(next: EntityDomain | undefined) {
    setDomain(next)
    setStatus('')
    setTag('')
    clearSelection()
  }

  function handleShowArchivedChange(next: boolean) {
    setShowArchived(next)
    clearSelection()
  }

  function handleStatusChange(next: string) {
    setStatus(next)
    clearSelection()
  }

  function handleTagChange(next: string) {
    setTag(next)
    clearSelection()
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const statusOptions = domain
    ? DOMAIN_REGISTRY[domain].statuses
    : Array.from(new Set(DOMAINS.flatMap((d) => DOMAIN_REGISTRY[d].statuses))).sort()

  const entities = entitiesQuery.data?.filter((e) => !status || e.status === status)
  const visibleEntities = entities ?? []

  const allSelected = visibleEntities.length > 0 && visibleEntities.every((e) => selected.has(e.id))
  const someSelected = visibleEntities.some((e) => selected.has(e.id)) && !allSelected

  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = someSelected
  }, [someSelected])

  function toggleSelectAll() {
    setBulkResult(null)
    setSelected(allSelected ? new Set() : new Set(visibleEntities.map((e) => e.id)))
  }

  function handleBulkArchive() {
    setBulkResult(null)
    bulkArchive.mutate([...selected], {
      onSuccess: (result) => {
        setBulkResult({
          action: 'archive',
          succeeded: result.succeeded.length,
          failed: result.not_found.length + result.forbidden.length,
        })
        setSelected(new Set())
      },
    })
  }

  function handleBulkRestore() {
    setBulkResult(null)
    bulkRestore.mutate([...selected], {
      onSuccess: (result) => {
        setBulkResult({
          action: 'restore',
          succeeded: result.succeeded.length,
          failed: result.not_found.length + result.forbidden.length,
        })
        setSelected(new Set())
      },
    })
  }

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Entities</h1>
        <button
          type="button"
          onClick={() => setShowCreate((v) => !v)}
          className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
        >
          {showCreate ? 'Cancel' : 'Add entity'}
        </button>
      </div>

      {showCreate && (
        <div className="mt-4 rounded-lg border border-divider p-4">
          <EntityForm
            isSubmitting={createEntity.isPending}
            submitError={createEntity.error instanceof ApiError ? createEntity.error.message : null}
            onSubmit={(input) => createEntity.mutate(input, { onSuccess: () => setShowCreate(false) })}
          />
        </div>
      )}

      <div className="mt-4 flex items-center gap-2 flex-wrap">
        {DOMAIN_FILTERS.map((f) => (
          <button
            key={f.label}
            type="button"
            onClick={() => handleDomainChange(f.value)}
            className={`rounded-md px-3 py-1 text-sm ${
              domain === f.value
                ? 'bg-active'
                : 'text-subtle hover:bg-surface-hover'
            }`}
          >
            {f.label}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-sm text-subtle">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => handleShowArchivedChange(e.target.checked)}
          />
          Show archived
        </label>
      </div>

      <div className="mt-2 flex items-center gap-2 flex-wrap">
        <select
          value={status}
          onChange={(e) => handleStatusChange(e.target.value)}
          className="rounded-md border border-line bg-transparent px-2 py-1 text-sm"
        >
          <option value="">All statuses</option>
          {statusOptions.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, ' ')}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => setTagModalOpen(true)}
          className="rounded-md border border-line bg-transparent px-2 py-1 text-sm text-left hover:bg-surface-hover"
        >
          {tag || 'All tags'}
        </button>
      </div>

      {tagModalOpen && (
        <TagFilterModal
          value={tag}
          onChange={handleTagChange}
          onClose={() => setTagModalOpen(false)}
          domain={domain}
          includeArchived={showArchived}
        />
      )}

      {visibleEntities.length > 0 && (
        <label className="mt-4 flex items-center gap-2 text-sm text-subtle">
          <input type="checkbox" ref={selectAllRef} checked={allSelected} onChange={toggleSelectAll} />
          Select all
        </label>
      )}

      {selected.size > 0 && (
        <div className="mt-2 flex items-center gap-3 rounded-lg border border-divider bg-surface-hover p-3">
          <span className="text-sm font-medium">{selected.size} selected</span>
          <button
            type="button"
            onClick={handleBulkArchive}
            disabled={bulkArchive.isPending || bulkRestore.isPending}
            className="rounded-md border border-line px-3 py-1.5 text-sm"
          >
            Archive
          </button>
          <button
            type="button"
            onClick={handleBulkRestore}
            disabled={bulkArchive.isPending || bulkRestore.isPending}
            className="rounded-md border border-line px-3 py-1.5 text-sm"
          >
            Restore
          </button>
          <button type="button" onClick={clearSelection} className="ml-auto text-sm text-subtle hover:underline">
            Clear
          </button>
        </div>
      )}

      {bulkResult && (
        <p className={`mt-2 text-sm ${bulkResult.failed > 0 ? 'text-red-500' : 'text-subtle'}`}>
          {bulkResult.action === 'archive' ? 'Archived' : 'Restored'} {bulkResult.succeeded} of{' '}
          {bulkResult.succeeded + bulkResult.failed} selected
          {bulkResult.failed > 0 ? ` — ${bulkResult.failed} failed.` : '.'}
        </p>
      )}

      <div className="mt-4 grid gap-2">
        {entitiesQuery.isPending && <p className="text-subtle">Loading…</p>}
        {entitiesQuery.isError && <p className="text-red-500">Failed to load entities.</p>}
        {entitiesQuery.isSuccess && entities?.length === 0 && (
          <p className="text-subtle">
            {entitiesQuery.data.length === 0
              ? 'No entities yet — add one to get started.'
              : 'No entities match these filters.'}
          </p>
        )}
        {entities?.map((entity) => (
          <div
            key={entity.id}
            className="rounded-lg border border-divider p-3 flex items-center gap-3 hover:bg-surface-hover"
          >
            <input
              type="checkbox"
              checked={selected.has(entity.id)}
              onChange={() => toggleSelected(entity.id)}
              className="shrink-0"
            />
            <Link to={`/entities/${entity.id}`} className="flex-1 flex items-center justify-between min-w-0">
              <div className="min-w-0">
                <p className="font-medium truncate">{entity.name}</p>
                <p className="text-sm text-subtle">
                  {entity.domain}
                  {entity.location ? ` · ${entity.location}` : ''}
                </p>
              </div>
              <StatusBadge status={entity.status} archived={entity.archived_at !== null} />
            </Link>
          </div>
        ))}
      </div>
    </div>
  )
}
