import type { Entity } from '../api/types'
import { useRestoreEntityFromTrash, useTrashedEntities } from '../hooks/useEntities'
import { ENTITY_TRASH_GRACE_DAYS, ENTITY_TRASH_GRACE_HOURS } from '../lib/trash'

function daysRemaining(pendingDeleteAt: string): number {
  // core-api serializes datetimes without a timezone designator (e.g.
  // "2026-07-30T17:50:27.841000") even though they're UTC at rest —
  // `new Date(...)` on a designator-less string parses it as *local* time,
  // silently shifting the result by the browser's UTC offset. Force UTC by
  // appending "Z" when no designator is already present.
  const isoUtc = /[Zz]|[+-]\d\d:\d\d$/.test(pendingDeleteAt) ? pendingDeleteAt : `${pendingDeleteAt}Z`
  const purgeAt = new Date(isoUtc).getTime() + ENTITY_TRASH_GRACE_HOURS * 60 * 60 * 1000
  return Math.max(0, Math.ceil((purgeAt - Date.now()) / 86_400_000))
}

export function RecentlyDeletedPage() {
  const trashQuery = useTrashedEntities()
  const restoreFromTrash = useRestoreEntityFromTrash()

  return (
    <div>
      <h1 className="text-2xl font-semibold">Recently Deleted</h1>
      <p className="mt-1 text-sm text-subtle">
        Deleted entities stay here for {ENTITY_TRASH_GRACE_DAYS} days before being permanently
        removed, along with their logs and schedules.
      </p>

      <div className="mt-4 grid gap-2">
        {trashQuery.isPending && <p className="text-subtle">Loading…</p>}
        {trashQuery.isError && <p className="text-red-500">Failed to load recently deleted entities.</p>}
        {trashQuery.isSuccess && trashQuery.data.length === 0 && (
          <p className="text-subtle">Nothing in the trash.</p>
        )}
        {trashQuery.data?.map((entity) => (
          <TrashedEntityRow
            key={entity.id}
            entity={entity}
            onRestore={() => restoreFromTrash.mutate(entity.id)}
            restoring={restoreFromTrash.isPending && restoreFromTrash.variables === entity.id}
          />
        ))}
      </div>
    </div>
  )
}

function TrashedEntityRow({
  entity,
  onRestore,
  restoring,
}: {
  entity: Entity
  onRestore: () => void
  restoring: boolean
}) {
  const remaining = entity.pending_delete_at ? daysRemaining(entity.pending_delete_at) : 0

  return (
    <div className="rounded-lg border border-divider p-3 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <p className="font-medium truncate">{entity.name}</p>
        <p className="text-sm text-subtle">
          {entity.domain}
          {entity.location ? ` · ${entity.location}` : ''}
        </p>
      </div>
      <span className={`text-xs font-medium shrink-0 ${remaining <= 1 ? 'text-red-500' : 'text-amber-600'}`}>
        Purges in {remaining} day{remaining === 1 ? '' : 's'}
      </span>
      <button
        type="button"
        onClick={onRestore}
        disabled={restoring}
        className="shrink-0 rounded-md border border-line px-3 py-1.5 text-sm hover:bg-surface-hover"
      >
        Restore
      </button>
    </div>
  )
}
