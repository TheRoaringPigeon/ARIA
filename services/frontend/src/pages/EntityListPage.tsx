import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ApiError } from '../api/client'
import { EntityForm } from '../components/EntityForm'
import { StatusBadge } from '../components/StatusBadge'
import { DOMAIN_REGISTRY, DOMAINS, type EntityDomain } from '../domains'
import { useCreateEntity, useEntities } from '../hooks/useEntities'

const DOMAIN_FILTERS: Array<{ label: string; value: EntityDomain | undefined }> = [
  { label: 'All', value: undefined },
  ...DOMAINS.map((d) => ({ label: DOMAIN_REGISTRY[d].label, value: d })),
]

export function EntityListPage() {
  const [domain, setDomain] = useState<EntityDomain | undefined>(undefined)
  const [showArchived, setShowArchived] = useState(false)
  const [showCreate, setShowCreate] = useState(false)

  const entitiesQuery = useEntities({ domain, include_archived: showArchived })
  const createEntity = useCreateEntity()

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
            onClick={() => setDomain(f.value)}
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
            onChange={(e) => setShowArchived(e.target.checked)}
          />
          Show archived
        </label>
      </div>

      <div className="mt-4 grid gap-2">
        {entitiesQuery.isPending && <p className="text-subtle">Loading…</p>}
        {entitiesQuery.isError && <p className="text-red-500">Failed to load entities.</p>}
        {entitiesQuery.data?.length === 0 && (
          <p className="text-subtle">No entities yet — add one to get started.</p>
        )}
        {entitiesQuery.data?.map((entity) => (
          <Link
            key={entity.id}
            to={`/entities/${entity.id}`}
            className="rounded-lg border border-divider p-3 flex items-center justify-between hover:bg-surface-hover"
          >
            <div>
              <p className="font-medium">{entity.name}</p>
              <p className="text-sm text-subtle">
                {entity.domain}
                {entity.location ? ` · ${entity.location}` : ''}
              </p>
            </div>
            <StatusBadge status={entity.status} archived={entity.archived_at !== null} />
          </Link>
        ))}
      </div>
    </div>
  )
}
