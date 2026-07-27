import { useEffect, useState } from 'react'
import { ApiError } from '../api/client'
import type { LogCreateInput } from '../api/logs'
import type { CalendarOccurrence, Entity } from '../api/types'
import { DOMAIN_REGISTRY, type EntityDomain } from '../domains'
import { useCreateEntity } from '../hooks/useEntities'
import { LogQueuedError, useCreateLog } from '../hooks/useLogs'
import { useCreateSchedule, useEntitySchedules } from '../hooks/useSchedules'
import { EntityCombobox } from './EntityCombobox'
import { EntityForm } from './EntityForm'
import { LogForm } from './LogForm'
import { ScheduleForm } from './ScheduleForm'

interface Props {
  date: string
  domain?: EntityDomain
  existingOccurrences: CalendarOccurrence[]
  onClose: () => void
}

type Step = 'summary' | 'pick-entity' | 'create-entity' | 'compose' | 'mark-done'
type ComposeType = 'schedule' | 'log'

// today's date as an ISO string, for comparing against occurrence_date —
// occurrence_date is a plain YYYY-MM-DD, so string comparison is safe.
function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

export function DayComposer({ date, domain, existingOccurrences, onClose }: Props) {
  const [step, setStep] = useState<Step>(existingOccurrences.length > 0 ? 'summary' : 'pick-entity')
  const [selectedEntity, setSelectedEntity] = useState<Entity | null>(null)
  const [composeType, setComposeType] = useState<ComposeType>('schedule')
  const [markingDoneOccurrence, setMarkingDoneOccurrence] = useState<CalendarOccurrence | null>(null)

  const createEntity = useCreateEntity()
  const createSchedule = useCreateSchedule()
  const createLog = useCreateLog()
  const entitySchedulesQuery = useEntitySchedules(selectedEntity?.id ?? markingDoneOccurrence?.entity_id)

  // Both log-composing paths (a fresh one-off entry and marking an existing
  // occurrence done) share the same LogQueuedError handling: useCreateLog
  // throws it (instead of resolving) when the request gets queued for
  // offline sync, so onSuccess alone would leave the modal open with no
  // feedback — see EntityDetailPage.tsx's createLog.mutate call sites for
  // the same pattern.
  function submitLog(input: LogCreateInput) {
    createLog.mutate(input, {
      onSuccess: onClose,
      onError: (error) => {
        if (error instanceof LogQueuedError) onClose()
      },
    })
  }

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  function selectEntity(entity: Entity) {
    setSelectedEntity(entity)
    setComposeType(DOMAIN_REGISTRY[entity.domain].uiVariant === 'plan' ? 'log' : 'schedule')
    setStep('compose')
  }

  return (
    <div className="fixed inset-0 z-20 flex items-start justify-center bg-black/40 pt-16" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-lg border border-divider bg-surface shadow-lg p-4 max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">{date}</h2>
          <button type="button" onClick={onClose} className="text-sm text-subtle hover:underline">
            Close
          </button>
        </div>

        {step === 'summary' && (
          <div className="mt-3 space-y-2">
            {existingOccurrences.map((occ) => {
              const isOverdue = occ.is_next_due && occ.occurrence_date < todayISO()
              return (
                <div
                  key={`${occ.schedule_id}-${occ.occurrence_date}`}
                  className="rounded-md border border-divider p-2 flex items-center justify-between gap-2"
                >
                  <div>
                    <p className="font-medium text-sm">{occ.title}</p>
                    <p
                      className={`text-xs ${
                        isOverdue ? 'text-red-500' : occ.is_next_due ? 'text-amber-600' : 'text-subtle'
                      }`}
                    >
                      {occ.entity_name} · {DOMAIN_REGISTRY[occ.domain].label}
                      {isOverdue ? ' · overdue' : occ.is_next_due ? ' · next due' : ''}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setMarkingDoneOccurrence(occ)
                      setStep('mark-done')
                    }}
                    className="shrink-0 rounded-md border border-line px-2 py-1 text-xs hover:bg-surface-hover"
                  >
                    Mark as done
                  </button>
                </div>
              )
            })}
            <button
              type="button"
              onClick={() => setStep('pick-entity')}
              className="mt-2 rounded-md bg-primary text-white hover:bg-primary-hover px-3 py-1.5 text-sm font-medium"
            >
              Add another
            </button>
          </div>
        )}

        {step === 'mark-done' && markingDoneOccurrence && (
          <div className="mt-3">
            <p className="text-sm text-subtle">
              Marking <span className="font-medium">{markingDoneOccurrence.title}</span> done for{' '}
              <span className="font-medium">{markingDoneOccurrence.entity_name}</span>
            </p>
            <div className="mt-2">
              <LogForm
                entityId={markingDoneOccurrence.entity_id}
                domain={markingDoneOccurrence.domain}
                schedules={entitySchedulesQuery.data ?? []}
                initialLog={{
                  occurred_at: date,
                  title: markingDoneOccurrence.title,
                  schedule_id: markingDoneOccurrence.schedule_id,
                }}
                submitLabel="Log it"
                isSubmitting={createLog.isPending}
                submitError={createLog.error instanceof ApiError ? createLog.error.message : null}
                onSubmit={submitLog}
              />
            </div>
            <button
              type="button"
              onClick={() => {
                setMarkingDoneOccurrence(null)
                setStep('summary')
              }}
              className="mt-2 text-sm text-subtle hover:underline"
            >
              Back
            </button>
          </div>
        )}

        {step === 'pick-entity' && (
          <div className="mt-3 space-y-2">
            <p className="text-sm text-subtle">Which entity is this for?</p>
            <EntityCombobox domain={domain} onSelect={selectEntity} />
            <button
              type="button"
              onClick={() => setStep('create-entity')}
              className="text-sm text-subtle hover:underline"
            >
              Can't find it? Create a new entity
            </button>
          </div>
        )}

        {step === 'create-entity' && (
          <div className="mt-3">
            <EntityForm
              initialDomain={domain}
              isSubmitting={createEntity.isPending}
              submitError={createEntity.error instanceof ApiError ? createEntity.error.message : null}
              submitLabel="Create entity"
              onSubmit={(input) => createEntity.mutate(input, { onSuccess: selectEntity })}
            />
            <button
              type="button"
              onClick={() => setStep('pick-entity')}
              className="mt-2 text-sm text-subtle hover:underline"
            >
              Back
            </button>
          </div>
        )}

        {step === 'compose' && selectedEntity && (
          <div className="mt-3">
            <p className="text-sm text-subtle">
              For <span className="font-medium">{selectedEntity.name}</span>
            </p>

            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => setComposeType('schedule')}
                className={`rounded-md px-3 py-1 text-sm ${
                  composeType === 'schedule' ? 'bg-active' : 'text-subtle hover:bg-surface-hover'
                }`}
              >
                Recurring schedule
              </button>
              <button
                type="button"
                onClick={() => setComposeType('log')}
                className={`rounded-md px-3 py-1 text-sm ${
                  composeType === 'log' ? 'bg-active' : 'text-subtle hover:bg-surface-hover'
                }`}
              >
                One-off / mark as done
              </button>
            </div>

            <div className="mt-3">
              {composeType === 'schedule' ? (
                <ScheduleForm
                  entityId={selectedEntity.id}
                  variant={DOMAIN_REGISTRY[selectedEntity.domain].uiVariant}
                  initialDate={date}
                  isSubmitting={createSchedule.isPending}
                  submitError={createSchedule.error instanceof ApiError ? createSchedule.error.message : null}
                  onSubmit={(input) => createSchedule.mutate(input, { onSuccess: onClose })}
                />
              ) : (
                <LogForm
                  entityId={selectedEntity.id}
                  domain={selectedEntity.domain}
                  schedules={entitySchedulesQuery.data ?? []}
                  initialLog={{ occurred_at: date }}
                  isSubmitting={createLog.isPending}
                  submitError={createLog.error instanceof ApiError ? createLog.error.message : null}
                  onSubmit={submitLog}
                />
              )}
            </div>

            <button
              type="button"
              onClick={() => setStep('pick-entity')}
              className="mt-2 text-sm text-subtle hover:underline"
            >
              Change entity
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
