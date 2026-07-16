import { useState, type ReactNode } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ApiError } from '../api/client'
import { DocumentList } from '../components/DocumentList'
import { DocumentUploadForm } from '../components/DocumentUploadForm'
import { EntityForm } from '../components/EntityForm'
import { LogForm } from '../components/LogForm'
import { ScheduleForm } from '../components/ScheduleForm'
import { StatusBadge } from '../components/StatusBadge'
import { useDeleteDocument } from '../hooks/useDeleteDocument'
import { useArchiveEntity, useDeleteEntity, useEntity, useRestoreEntity, useUpdateEntity } from '../hooks/useEntities'
import { useEntityDocuments } from '../hooks/useEntityDocuments'
import { useCreateLog, useDeleteLog, useEntityLogs, useUpdateLog } from '../hooks/useLogs'
import { useCreateSchedule, useDeleteSchedule, useEntitySchedules, useUpdateSchedule } from '../hooks/useSchedules'
import { useUploadDocument } from '../hooks/useUploadDocument'
import { describeRecurrence } from '../lib/recurrence'
import { DOMAIN_REGISTRY } from '../domains'

type Tab = 'logs' | 'schedules' | 'documents'

export function EntityDetailPage() {
  const { entityId } = useParams<{ entityId: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('logs')
  const [editing, setEditing] = useState(false)
  const [showLogForm, setShowLogForm] = useState(false)
  const [editingLogId, setEditingLogId] = useState<string | null>(null)
  const [showDocumentForm, setShowDocumentForm] = useState(false)

  const [showScheduleForm, setShowScheduleForm] = useState(false)
  const [markingDoneScheduleId, setMarkingDoneScheduleId] = useState<string | null>(null)
  const [editingPlanId, setEditingPlanId] = useState<string | null>(null)
  const [editingScheduleId, setEditingScheduleId] = useState<string | null>(null)

  const entityQuery = useEntity(entityId)
  const updateEntity = useUpdateEntity(entityId ?? '')
  const archiveEntity = useArchiveEntity()
  const restoreEntity = useRestoreEntity()
  const deleteEntity = useDeleteEntity()

  const logsQuery = useEntityLogs(entityId)
  const createLog = useCreateLog()
  const updateLog = useUpdateLog(entityId ?? '')
  const deleteLog = useDeleteLog(entityId ?? '')

  const schedulesQuery = useEntitySchedules(entityId)
  const createSchedule = useCreateSchedule()
  const updateSchedule = useUpdateSchedule(entityId ?? '')
  const deleteSchedule = useDeleteSchedule(entityId ?? '')

  const documentsQuery = useEntityDocuments(entityId)
  const uploadDocument = useUploadDocument(entityId ?? '')
  const deleteDocument = useDeleteDocument(entityId ?? '')

  if (entityQuery.isPending) return <p className="text-subtle">Loading…</p>
  if (entityQuery.isError || !entityQuery.data) return <p className="text-red-500">Entity not found.</p>

  const entity = entityQuery.data
  const archived = entity.archived_at !== null
  const usesPlansUI = DOMAIN_REGISTRY[entity.domain].uiVariant === 'plan'

  return (
    <div>
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{entity.name}</h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-subtle">
            <span>{entity.domain}</span>
            {entity.location && <span>· {entity.location}</span>}
            <StatusBadge status={entity.status} archived={archived} />
          </div>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setEditing((v) => !v)}
            className="rounded-md border border-line px-3 py-1.5 text-sm"
          >
            {editing ? 'Cancel' : 'Edit'}
          </button>
          {archived ? (
            <button
              type="button"
              onClick={() => restoreEntity.mutate(entity.id)}
              className="rounded-md border border-line px-3 py-1.5 text-sm"
            >
              Restore
            </button>
          ) : (
            <button
              type="button"
              onClick={() => archiveEntity.mutate(entity.id)}
              className="rounded-md border border-line px-3 py-1.5 text-sm"
            >
              Archive
            </button>
          )}
          <button
            type="button"
            onClick={() => {
              if (
                window.confirm(
                  'Permanently delete this entity and all of its history and schedules? This cannot be undone.',
                )
              ) {
                deleteEntity.mutate(entity.id, { onSuccess: () => navigate('/entities') })
              }
            }}
            className="rounded-md border border-line px-3 py-1.5 text-sm text-red-500"
          >
            Delete
          </button>
        </div>
      </div>

      {editing && (
        <div className="mt-4 rounded-lg border border-divider p-4">
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

      <div className="mt-6 flex gap-2 border-b border-divider">
        <TabButton active={tab === 'logs'} onClick={() => setTab('logs')}>
          History
        </TabButton>
        <TabButton active={tab === 'schedules'} onClick={() => setTab('schedules')}>
          {usesPlansUI ? 'Plans' : 'Schedules'}
        </TabButton>
        <TabButton active={tab === 'documents'} onClick={() => setTab('documents')}>
          Documents
        </TabButton>
      </div>

      {tab === 'logs' && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowLogForm((v) => !v)}
              className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
            >
              {showLogForm ? 'Cancel' : 'Add log entry'}
            </button>
          </div>
          {showLogForm && (
            <div className="mt-3 rounded-lg border border-divider p-4">
              <LogForm
                entityId={entity.id}
                domain={entity.domain}
                schedules={schedulesQuery.data ?? []}
                isSubmitting={createLog.isPending}
                submitError={createLog.error instanceof ApiError ? createLog.error.message : null}
                onSubmit={(input) => createLog.mutate(input, { onSuccess: () => setShowLogForm(false) })}
              />
            </div>
          )}

          <div className="mt-4 grid gap-2">
            {logsQuery.isPending && <p className="text-subtle">Loading…</p>}
            {logsQuery.data?.length === 0 && <p className="text-subtle">No history yet.</p>}
            {logsQuery.data?.map((log) =>
              editingLogId === log.id ? (
                <div key={log.id} className="rounded-lg border border-divider p-4">
                  <LogForm
                    entityId={entity.id}
                    domain={entity.domain}
                    schedules={schedulesQuery.data ?? []}
                    initialLog={log}
                    submitLabel="Save changes"
                    isSubmitting={updateLog.isPending}
                    submitError={updateLog.error instanceof ApiError ? updateLog.error.message : null}
                    onSubmit={({ entity_id: _entityId, ...updateInput }) =>
                      updateLog.mutate({ id: log.id, input: updateInput }, { onSuccess: () => setEditingLogId(null) })
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setEditingLogId(null)}
                    className="mt-2 text-sm text-subtle"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div key={log.id} className="rounded-lg border border-divider p-3">
                  <div className="flex items-start justify-between gap-3">
                    <p className="font-medium">{log.title}</p>
                    <div className="flex items-center gap-2 shrink-0">
                      <span className="text-sm text-subtle">{log.occurred_at}</span>
                      <button
                        type="button"
                        onClick={() => setEditingLogId(log.id)}
                        className="text-sm text-subtle hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (window.confirm('Delete this log entry? This cannot be undone.')) {
                            deleteLog.mutate(log.id)
                          }
                        }}
                        className="text-sm text-red-500 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <p className="text-sm text-subtle">
                    {log.type}
                    {log.cost != null ? ` · $${log.cost}` : ''}
                  </p>
                  {log.description && <p className="mt-1 text-sm">{log.description}</p>}
                </div>
              ),
            )}
          </div>
        </div>
      )}

      {tab === 'schedules' && usesPlansUI && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowScheduleForm((v) => !v)}
              className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
            >
              {showScheduleForm ? 'Cancel' : 'Add plan'}
            </button>
          </div>
          {showScheduleForm && (
            <div className="mt-3 rounded-lg border border-divider p-4">
              <ScheduleForm
                entityId={entity.id}
                variant="plan"
                isSubmitting={createSchedule.isPending}
                submitError={createSchedule.error instanceof ApiError ? createSchedule.error.message : null}
                onSubmit={(input) =>
                  createSchedule.mutate(input, { onSuccess: () => setShowScheduleForm(false) })
                }
              />
            </div>
          )}

          <div className="mt-4 grid gap-2">
            {schedulesQuery.isPending && <p className="text-subtle">Loading…</p>}
            {schedulesQuery.data?.length === 0 && <p className="text-subtle">No plans yet.</p>}
            {schedulesQuery.data?.map((plan) =>
              editingPlanId === plan.id ? (
                <div key={plan.id} className="rounded-lg border border-divider p-4">
                  <ScheduleForm
                    entityId={entity.id}
                    variant="plan"
                    initialSchedule={plan}
                    submitLabel="Save changes"
                    isSubmitting={updateSchedule.isPending}
                    submitError={updateSchedule.error instanceof ApiError ? updateSchedule.error.message : null}
                    onSubmit={({ entity_id: _eid, ...updateInput }) =>
                      updateSchedule.mutate(
                        { id: plan.id, input: updateInput },
                        { onSuccess: () => setEditingPlanId(null) },
                      )
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setEditingPlanId(null)}
                    className="mt-2 text-sm text-subtle"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div key={plan.id} className="rounded-lg border border-divider p-3">
                  <div className="flex items-center justify-between">
                    <p className="font-medium">{plan.title}</p>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        type="button"
                        onClick={() => setMarkingDoneScheduleId(plan.id)}
                        className="text-sm text-subtle hover:underline"
                      >
                        Mark as done
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingPlanId(plan.id)}
                        className="text-sm text-subtle hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (window.confirm('Delete this plan? This cannot be undone.')) {
                            deleteSchedule.mutate(plan.id)
                          }
                        }}
                        className="text-sm text-red-500 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <p className="text-sm text-subtle">{describeRecurrence(plan)}</p>
                  <p className="text-sm text-subtle">
                    {plan.next_due_at
                      ? `Planned: ${plan.next_due_at}${plan.planned_time ? ` at ${plan.planned_time}` : ''}`
                      : 'Done'}
                  </p>

                  {markingDoneScheduleId === plan.id && (
                    <div className="mt-3 border-t border-divider pt-3">
                      <LogForm
                        entityId={entity.id}
                        domain={entity.domain}
                        schedules={schedulesQuery.data ?? []}
                        initialLog={{
                          type: 'meeting',
                          occurred_at: new Date().toISOString().slice(0, 10),
                          title: plan.title,
                          schedule_id: plan.id,
                        }}
                        submitLabel="Log it"
                        isSubmitting={createLog.isPending}
                        submitError={createLog.error instanceof ApiError ? createLog.error.message : null}
                        onSubmit={(input) =>
                          createLog.mutate(input, {
                            onSuccess: () => {
                              setMarkingDoneScheduleId(null)
                              setTab('logs')
                            },
                          })
                        }
                      />
                      <button
                        type="button"
                        onClick={() => setMarkingDoneScheduleId(null)}
                        className="mt-2 text-sm text-subtle"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </div>
              ),
            )}
          </div>
        </div>
      )}

      {tab === 'schedules' && !usesPlansUI && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowScheduleForm((v) => !v)}
              className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
            >
              {showScheduleForm ? 'Cancel' : 'Add schedule'}
            </button>
          </div>
          {showScheduleForm && (
            <div className="mt-3 rounded-lg border border-divider p-4">
              <ScheduleForm
                entityId={entity.id}
                variant="schedule"
                isSubmitting={createSchedule.isPending}
                submitError={createSchedule.error instanceof ApiError ? createSchedule.error.message : null}
                onSubmit={(input) =>
                  createSchedule.mutate(input, { onSuccess: () => setShowScheduleForm(false) })
                }
              />
            </div>
          )}

          <div className="mt-4 grid gap-2">
            {schedulesQuery.isPending && <p className="text-subtle">Loading…</p>}
            {schedulesQuery.data?.length === 0 && <p className="text-subtle">No schedules yet.</p>}
            {schedulesQuery.data?.map((schedule) =>
              editingScheduleId === schedule.id ? (
                <div key={schedule.id} className="rounded-lg border border-divider p-4">
                  <ScheduleForm
                    entityId={entity.id}
                    variant="schedule"
                    initialSchedule={schedule}
                    submitLabel="Save changes"
                    isSubmitting={updateSchedule.isPending}
                    submitError={updateSchedule.error instanceof ApiError ? updateSchedule.error.message : null}
                    onSubmit={({ entity_id: _eid, ...updateInput }) =>
                      updateSchedule.mutate(
                        { id: schedule.id, input: updateInput },
                        { onSuccess: () => setEditingScheduleId(null) },
                      )
                    }
                  />
                  <button
                    type="button"
                    onClick={() => setEditingScheduleId(null)}
                    className="mt-2 text-sm text-subtle"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div key={schedule.id} className="rounded-lg border border-divider p-3">
                  <div className="flex items-center justify-between">
                    <p className="font-medium">{schedule.title}</p>
                    <div className="flex items-center gap-2 shrink-0">
                      {!schedule.active && <span className="text-xs text-subtle">inactive</span>}
                      <button
                        type="button"
                        onClick={() => setEditingScheduleId(schedule.id)}
                        className="text-sm text-subtle hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (window.confirm('Delete this schedule? This cannot be undone.')) {
                            deleteSchedule.mutate(schedule.id)
                          }
                        }}
                        className="text-sm text-red-500 hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <p className="text-sm text-subtle">{describeRecurrence(schedule)}</p>
                  <p className="text-sm text-subtle">
                    Next due: {schedule.next_due_at ?? schedule.next_due_usage_value ?? 'unknown'}
                  </p>
                </div>
              ),
            )}
          </div>
        </div>
      )}

      {tab === 'documents' && (
        <div className="mt-4">
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setShowDocumentForm((v) => !v)}
              className="rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
            >
              {showDocumentForm ? 'Cancel' : 'Upload document'}
            </button>
          </div>
          {showDocumentForm && (
            <div className="mt-3 rounded-lg border border-divider p-4">
              <DocumentUploadForm
                isSubmitting={uploadDocument.isPending}
                submitError={uploadDocument.error instanceof ApiError ? uploadDocument.error.message : null}
                onSubmit={(input) => uploadDocument.mutate(input, { onSuccess: () => setShowDocumentForm(false) })}
              />
            </div>
          )}

          <div className="mt-4">
            {documentsQuery.isPending && <p className="text-subtle">Loading…</p>}
            {documentsQuery.data && (
              <DocumentList documents={documentsQuery.data} onDelete={(id) => deleteDocument.mutate(id)} />
            )}
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
        active ? 'border-primary' : 'border-transparent text-subtle'
      }`}
    >
      {children}
    </button>
  )
}
