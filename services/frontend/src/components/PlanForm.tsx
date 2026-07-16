import { useState, type FormEvent } from 'react'
import type { ScheduleCreateInput } from '../api/schedules'
import type { Schedule } from '../api/types'
import { recurrenceModeOf, WEEK_INDEXES, WEEKDAYS, type RecurrenceMode } from '../lib/recurrence'

interface Props {
  entityId: string
  initialPlan?: Schedule
  onSubmit: (input: ScheduleCreateInput) => void
  isSubmitting?: boolean
  submitError?: string | null
  submitLabel?: string
}

export function PlanForm({ entityId, initialPlan, onSubmit, isSubmitting, submitError, submitLabel }: Props) {
  const isEdit = initialPlan !== undefined
  // interval_type (and, for recurring plans, which recurrence mode) is
  // immutable once a plan exists — same rationale as domain on entities.
  // To switch a plan to a different kind of recurrence, delete it and
  // create a new one; editing only tweaks that mode's own parameters.
  const [title, setTitle] = useState(initialPlan?.title ?? '')
  const [date, setDate] = useState(
    () => initialPlan?.planned_at ?? new Date().toISOString().slice(0, 10),
  )
  const [time, setTime] = useState(initialPlan?.planned_time ?? '')
  const [recurring, setRecurring] = useState(
    initialPlan?.interval_type === 'time' || initialPlan?.interval_type === 'monthly',
  )
  const [recurrenceMode, setRecurrenceMode] = useState<RecurrenceMode>(() => recurrenceModeOf(initialPlan))
  const [intervalDays, setIntervalDays] = useState(String(initialPlan?.interval_days ?? 30))
  const [monthlyDay, setMonthlyDay] = useState(String(initialPlan?.monthly_day ?? 1))
  const [monthlyWeekIndex, setMonthlyWeekIndex] = useState(String(initialPlan?.monthly_week_index ?? 2))
  const [monthlyWeekday, setMonthlyWeekday] = useState(String(initialPlan?.monthly_weekday ?? 4))

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const base = { entity_id: entityId, title, planned_time: time || null }

    if (!recurring) {
      onSubmit({ ...base, interval_type: 'once', planned_at: date })
      return
    }
    if (recurrenceMode === 'days') {
      onSubmit({ ...base, interval_type: 'time', interval_days: Number(intervalDays), starting_at: date || null })
    } else if (recurrenceMode === 'monthly_day') {
      onSubmit({ ...base, interval_type: 'monthly', monthly_day: Number(monthlyDay), starting_at: date || null })
    } else {
      onSubmit({
        ...base,
        interval_type: 'monthly',
        monthly_weekday: Number(monthlyWeekday),
        monthly_week_index: Number(monthlyWeekIndex),
        starting_at: date || null,
      })
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <label className="block">
        <span className="text-sm">What's planned</span>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Coffee with Sandra"
          className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
        />
      </label>

      {isEdit && recurring ? (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Time (optional)</span>
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            />
          </label>
          <p className="text-sm text-subtle mt-6">Recurring plan</p>
        </div>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          <label className="block">
            <span className="text-sm">Date</span>
            <input
              type="date"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            />
          </label>
          <label className="block">
            <span className="text-sm">Time (optional)</span>
            <input
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            />
          </label>
          {isEdit ? (
            <p className="text-sm text-subtle mt-6">One-time plan</p>
          ) : (
            <label className="flex items-center gap-2 mt-6 text-sm">
              <input type="checkbox" checked={recurring} onChange={(e) => setRecurring(e.target.checked)} />
              Recurring
            </label>
          )}
        </div>
      )}

      {recurring && !isEdit && (
        <label className="block">
          <span className="text-sm">Repeats</span>
          <select
            value={recurrenceMode}
            onChange={(e) => setRecurrenceMode(e.target.value as RecurrenceMode)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
          >
            <option value="days">Every N days</option>
            <option value="monthly_day">Monthly, on a specific day</option>
            <option value="monthly_weekday">Monthly, on a specific weekday (e.g. 2nd Friday)</option>
          </select>
        </label>
      )}

      {recurring && recurrenceMode === 'days' && (
        <label className="block">
          <span className="text-sm">Every (days)</span>
          <input
            type="number"
            required
            min={1}
            value={intervalDays}
            onChange={(e) => setIntervalDays(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
          />
        </label>
      )}

      {recurring && recurrenceMode === 'monthly_day' && (
        <label className="block">
          <span className="text-sm">Day of month</span>
          <input
            type="number"
            required
            min={1}
            max={31}
            value={monthlyDay}
            onChange={(e) => setMonthlyDay(e.target.value)}
            className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
          />
          <span className="mt-1 block text-xs text-subtle">
            Months without this day (e.g. the 31st in February) use the last day of that month instead.
          </span>
        </label>
      )}

      {recurring && recurrenceMode === 'monthly_weekday' && (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Which</span>
            <select
              value={monthlyWeekIndex}
              onChange={(e) => setMonthlyWeekIndex(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            >
              {WEEK_INDEXES.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-sm">Weekday</span>
            <select
              value={monthlyWeekday}
              onChange={(e) => setMonthlyWeekday(e.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            >
              {WEEKDAYS.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : (submitLabel ?? 'Add plan')}
      </button>
    </form>
  )
}
