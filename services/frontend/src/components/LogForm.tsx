import { useState, type FormEvent } from 'react'
import type { LogCreateInput } from '../api/logs'
import { LOG_TYPES_BY_DOMAIN, type EntityDomain, type LogEntry, type LogType, type Schedule } from '../api/types'

interface Props {
  entityId: string
  domain: EntityDomain
  schedules: Schedule[]
  initialLog?: Partial<LogEntry>
  onSubmit: (input: LogCreateInput) => void
  isSubmitting?: boolean
  submitError?: string | null
  submitLabel?: string
}

export function LogForm({
  entityId,
  domain,
  schedules,
  initialLog,
  onSubmit,
  isSubmitting,
  submitError,
  submitLabel,
}: Props) {
  const availableTypes = LOG_TYPES_BY_DOMAIN[domain]
  const showCostAndSchedule = domain !== 'person'
  const showMetrics = domain !== 'person'

  const [type, setType] = useState<LogType>(initialLog?.type ?? availableTypes[0])
  const [occurredAt, setOccurredAt] = useState(
    () => initialLog?.occurred_at ?? new Date().toISOString().slice(0, 10),
  )
  const [title, setTitle] = useState(initialLog?.title ?? '')
  const [description, setDescription] = useState(initialLog?.description ?? '')
  const [cost, setCost] = useState(initialLog?.cost != null ? String(initialLog.cost) : '')
  const [scheduleId, setScheduleId] = useState(initialLog?.schedule_id ?? '')
  const [metricsText, setMetricsText] = useState(
    initialLog?.metrics ? Object.entries(initialLog.metrics).map(([k, v]) => `${k}: ${v}`).join('\n') : '',
  )

  const activeSchedules = schedules.filter((s) => s.active)
  const selectedSchedule = activeSchedules.find((s) => s.id === scheduleId)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const metrics: Record<string, string> = {}
    for (const line of metricsText.split('\n')) {
      const [key, ...rest] = line.split(':')
      if (key && rest.length > 0) {
        metrics[key.trim()] = rest.join(':').trim()
      }
    }

    onSubmit({
      entity_id: entityId,
      type,
      occurred_at: occurredAt,
      title,
      description: description.trim() === '' ? null : description,
      cost: showCostAndSchedule && cost.trim() !== '' ? Number(cost) : null,
      metrics: showMetrics ? metrics : {},
      schedule_id: showCostAndSchedule && scheduleId !== '' ? scheduleId : null,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-sm">Type</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value as LogType)}
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
          >
            {availableTypes.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-sm">Date</span>
          <input
            type="date"
            required
            value={occurredAt}
            onChange={(e) => setOccurredAt(e.target.value)}
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
          />
        </label>
      </div>

      <label className="block">
        <span className="text-sm">Title</span>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={domain === 'person' ? 'Coffee catch-up' : 'Oil change'}
          className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
        />
      </label>

      <label className="block">
        <span className="text-sm">Description</span>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
        />
      </label>

      {showCostAndSchedule && (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Cost</span>
            <input
              type="number"
              step="0.01"
              value={cost}
              onChange={(e) => setCost(e.target.value)}
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="block">
            <span className="text-sm">Completes schedule</span>
            <select
              value={scheduleId}
              onChange={(e) => setScheduleId(e.target.value)}
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            >
              <option value="">None</option>
              {activeSchedules.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {showCostAndSchedule && selectedSchedule?.interval_type === 'usage' && (
        <p className="text-xs text-neutral-500">
          This schedule tracks <code>{selectedSchedule.usage_metric}</code> — include it as a metric
          below, e.g. <code>{selectedSchedule.usage_metric}: 42000</code>.
        </p>
      )}

      {showMetrics && (
        <label className="block">
          <span className="text-sm">Metrics (one per line, key: value)</span>
          <textarea
            value={metricsText}
            onChange={(e) => setMetricsText(e.target.value)}
            rows={2}
            placeholder="odometer_reading: 42000"
            className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5 font-mono text-xs"
          />
        </label>
      )}

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-md bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : (submitLabel ?? 'Add log entry')}
      </button>
    </form>
  )
}
