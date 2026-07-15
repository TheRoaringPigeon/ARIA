import { useState, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { EntityForm } from '../components/EntityForm'
import { LogForm } from '../components/LogForm'
import { ScheduleForm } from '../components/ScheduleForm'
import { StatusBadge } from '../components/StatusBadge'
import { useArchiveEntity, useEntity, useRestoreEntity, useUpdateEntity } from '../hooks/useEntities'
import { useCreateLog, useEntityLogs } from '../hooks/useLogs'
import { useCreateSchedule, useEntitySchedules } from '../hooks/useSchedules'

type Tab = 'logs' | 'schedules'

export function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>()
  const [tab, setTab] = useState<Tab>('logs')
  const [editing, setEditing] = useState(false)
  const [showLogForm, setShowLogForm] = useState(false)
  const [showScheduleForm, setShowScheduleForm] = useState(false)

  const entityQuery = useEntity(entityId)
  const updateEntity = useUpdateEntity(entityId ?? '')
  const archiveEntity = useArchiveEntity()
  const restoreEntity = useRestoreEntity()

  const logsQuery = useEntityLogs(entityId)
  const createLog = useCreateLog()

  const schedulesQuery = useEntitySchedules(entityId)
  const createSchedule = useCreateSchedule()

  if (entityQuery.isPending) return <p className="text-neutral-500">Loading…</p>
  if (entityQuery.isError || !entityQuery.data) return <p className="text-red-500">Entity not found.</p>

  const entity = entityQuery.data
  const archived = entity.archived_at !== null

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{entity.name}</h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-neutral-500">
            <span>{entity.domain}</span>
            {entity.location && <span>· {entity.location}</span>}
            <StatusBadge status={entity.status} archived={archived} />
          </div>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="rounded-md border border-neutral-300 dark:border-neutral-600 px-3 py-1.5 text-sm"
          >
            {editing ? 'Cancel' : 'Edit'}
          </button>
          {archived ? (
            <button
              type="button"
              onClick={() => restoreEntity.mutate(entity.id)}
              className="rounded-md border border-neutral-300 dark:border-neutral-600 px-3 py-1.5 text-sm"
            >
              Restore
            </button>
          ) : (
            <button
              type="button"
              onClick={() => archiveEntity.mutate(entity.id)}
              className="rounded-md border border-neutral-300 dark:border-neutral-600 px-3 py-1.5 text-sm"
            >
              Archive
            </button>
          )}
        </div>
      </div>

      {editing && (
        <div className="mt-4 rounded-lg border border-neutral-200 dark:border-neutral-700 p-4">
          <EntityForm
            initialEntity={entity}
            isSubmitting={updateEntity.isPending}
            submitError={updateEntity.error instanceof ApiError ? updateEntity.error.message : null}
            onSubmit={({ domain: _domain, ...updateInput }) =>
              updateEntity.mutate(updateInput, { onSuccess: () => setEditing(false) })
            }
          />
        </div>
      )}

      <div className="mt-6 flex gap-2 border-b border-neutral-200 dark:border-neutral-700">
        <TabButton active={tab === 'logs'} onClick={() => setTab('logs')}>
          History
        </TabButton>
        <TabButton active={tab === 'schedules'} onClick={() => setTab('schedules')}>
          Schedules
        </TabButton>
      </div>

      {tab === 'logs' && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowLogForm((v) => !v)}
              className="rounded-md bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-3 py-1.5 text-sm font-medium"
            >
              {showLogForm ? 'Cancel' : 'Add log entry'}
            </button>
          </div>
          {showLogForm && (
            <div className="mt-3 rounded-lg border border-neutral-200 dark:border-neutral-700 p-4">
              <LogForm
                entityId={entity.id}
                schedules={schedulesQuery.data ?? []}
                isSubmitting={createLog.isPending}
                submitError={createLog.error instanceof ApiError ? createLog.error.message : null}
                onSubmit={(input) => createLog.mutate(input, { onSuccess: () => setShowLogForm(false) })}
              />
            </div>
          )}

          <div className="mt-4 grid gap-2">
            {logsQuery.isPending && <p className="text-neutral-500">Loading…</p>}
            {logsQuery.data?.length === 0 && <p className="text-neutral-500">No history yet.</p>}
            {logsQuery.data?.map((log) => (
              <div key={log.id} className="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3">
                <div className="flex items-center justify-between">
                  <p className="font-medium">{log.title}</p>
                  <span className="text-sm text-neutral-500">{log.occurred_at}</span>
                </div>
                <p className="text-sm text-neutral-500">
                  {log.type}
                  {log.cost != null ? ` · $${log.cost}` : ''}
                </p>
                {log.description && <p className="mt-1 text-sm">{log.description}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {tab === 'schedules' && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowScheduleForm((v) => !v)}
              className="rounded-md bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-3 py-1.5 text-sm font-medium"
            >
              {showScheduleForm ? 'Cancel' : 'Add schedule'}
            </button>
          </div>
          {showScheduleForm && (
            <div className="mt-3 rounded-lg border border-neutral-200 dark:border-neutral-700 p-4">
              <ScheduleForm
                entityId={entity.id}
                isSubmitting={createSchedule.isPending}
                submitError={createSchedule.error instanceof ApiError ? createSchedule.error.message : null}
                onSubmit={(input) =>
                  createSchedule.mutate(input, { onSuccess: () => setShowScheduleForm(false) })
                }
              />
            </div>
          )}

          <div className="mt-4 grid gap-2">
            {schedulesQuery.isPending && <p className="text-neutral-500">Loading…</p>}
            {schedulesQuery.data?.length === 0 && <p className="text-neutral-500">No schedules yet.</p>}
            {schedulesQuery.data?.map((schedule) => (
              <div
                key={schedule.id}
                className="rounded-lg border border-neutral-200 dark:border-neutral-700 p-3"
              >
                <div className="flex items-center justify-between">
                  <p className="font-medium">{schedule.title}</p>
                  {!schedule.active && <span className="text-xs text-neutral-500">inactive</span>}
                </div>
                <p className="text-sm text-neutral-500">
                  {schedule.interval_type === 'time'
                    ? `Every ${schedule.interval_days} days`
                    : `Every ${schedule.interval_usage_amount} ${schedule.usage_metric}`}
                </p>
                <p className="text-sm text-neutral-500">
                  Next due: {schedule.next_due_at ?? schedule.next_due_usage_value ?? 'unknown'}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px ${
        active ? 'border-neutral-900 dark:border-white' : 'border-transparent text-neutral-500'
      }`}
    >
      {children}
    </button>
  )
}
