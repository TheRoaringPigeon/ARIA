import { useState, type FormEvent } from 'react'
import type { ScheduleCreateInput } from '../api/schedules'
import type { IntervalType } from '../api/types'

interface Props {
  entityId: string
  onSubmit: (input: ScheduleCreateInput) => void
  isSubmitting?: boolean
  submitError?: string | null
}

export function ScheduleForm({ entityId, onSubmit, isSubmitting, submitError }: Props) {
  const [title, setTitle] = useState('')
  const [intervalType, setIntervalType] = useState<IntervalType>('time')
  const [intervalDays, setIntervalDays] = useState('90')
  const [usageMetric, setUsageMetric] = useState('')
  const [intervalUsageAmount, setIntervalUsageAmount] = useState('')
  const [startingAt, setStartingAt] = useState(() => new Date().toISOString().slice(0, 10))
  const [startingUsageValue, setStartingUsageValue] = useState('')

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (intervalType === 'time') {
      onSubmit({
        entity_id: entityId,
        title,
        interval_type: 'time',
        interval_days: Number(intervalDays),
        starting_at: startingAt || null,
      })
    } else {
      onSubmit({
        entity_id: entityId,
        title,
        interval_type: 'usage',
        usage_metric: usageMetric,
        interval_usage_amount: Number(intervalUsageAmount),
        starting_usage_value: startingUsageValue.trim() === '' ? null : Number(startingUsageValue),
      })
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <label className="block">
        <span className="text-sm">Title</span>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Oil change"
          className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
        />
      </label>

      <label className="block">
        <span className="text-sm">Recurrence</span>
        <select
          value={intervalType}
          onChange={(e) => setIntervalType(e.target.value as IntervalType)}
          className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
        >
          <option value="time">Time-based (e.g. every N days)</option>
          <option value="usage">Usage-based (e.g. every N miles)</option>
        </select>
      </label>

      {intervalType === 'time' ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Every (days)</span>
            <input
              type="number"
              required
              min={1}
              value={intervalDays}
              onChange={(e) => setIntervalDays(e.target.value)}
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="block">
            <span className="text-sm">Starting from</span>
            <input
              type="date"
              value={startingAt}
              onChange={(e) => setStartingAt(e.target.value)}
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Usage metric key</span>
            <input
              required
              value={usageMetric}
              onChange={(e) => setUsageMetric(e.target.value)}
              placeholder="odometer_reading"
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="block">
            <span className="text-sm">Every (amount)</span>
            <input
              type="number"
              required
              min={1}
              value={intervalUsageAmount}
              onChange={(e) => setIntervalUsageAmount(e.target.value)}
              placeholder="5000"
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="block col-span-2">
            <span className="text-sm">Current reading (optional)</span>
            <input
              type="number"
              value={startingUsageValue}
              onChange={(e) => setStartingUsageValue(e.target.value)}
              placeholder="e.g. current odometer reading"
              className="mt-1 w-full rounded-md border border-neutral-300 dark:border-neutral-600 bg-transparent px-2 py-1.5"
            />
          </label>
        </div>
      )}

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-md bg-neutral-900 text-white dark:bg-white dark:text-neutral-900 px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : 'Create schedule'}
      </button>
    </form>
  )
}
