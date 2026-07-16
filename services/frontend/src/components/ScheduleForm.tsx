import { useState, type FormEvent } from 'react'
import type { ScheduleCreateInput } from '../api/schedules'
import type { Schedule } from '../api/types'
import { recurrenceModeOf, WEEK_INDEXES, WEEKDAYS, type RecurrenceMode } from '../lib/recurrence'

interface Props {
  entityId: string
  variant: 'plan' | 'schedule'
  initialSchedule?: Schedule
  onSubmit: (input: ScheduleCreateInput) => void
  isSubmitting?: boolean
  submitError?: string | null
  submitLabel?: string
}

const COPY = {
  plan: { titleLabel: "What's planned", titlePlaceholder: 'Coffee with Sandra', submitLabel: 'Add plan' },
  schedule: { titleLabel: 'Title', titlePlaceholder: 'Oil change', submitLabel: 'Create schedule' },
}

export function ScheduleForm({
  entityId,
  variant,
  initialSchedule,
  onSubmit,
  isSubmitting,
  submitError,
  submitLabel,
}: Props) {
  const isPlan = variant === 'plan'
  const isEdit = initialSchedule !== undefined
  const copy = COPY[variant]

  // interval_type (and, for recurring schedules, which recurrence mode) is
  // immutable once a schedule exists — same rationale as domain on entities.
  // To switch to a different kind of recurrence, delete it and create a new
  // one; editing only tweaks that mode's own parameters.
  const [title, setTitle] = useState(initialSchedule?.title ?? '')
  const [date, setDate] = useState(
    () => initialSchedule?.planned_at ?? new Date().toISOString().slice(0, 10),
  )
  const [time, setTime] = useState(initialSchedule?.planned_time ?? '')
  const [mode, setMode] = useState<RecurrenceMode>(() =>
    isEdit ? recurrenceModeOf(initialSchedule) : isPlan ? 'once' : 'days',
  )
  const recurring = mode !== 'once'
  const [intervalDays, setIntervalDays] = useState(
    String(initialSchedule?.interval_days ?? (isPlan ? 30 : 90)),
  )
  const [monthlyDay, setMonthlyDay] = useState(String(initialSchedule?.monthly_day ?? 1))
  const [monthlyWeekIndex, setMonthlyWeekIndex] = useState(String(initialSchedule?.monthly_week_index ?? 2))
  const [monthlyWeekday, setMonthlyWeekday] = useState(String(initialSchedule?.monthly_weekday ?? 4))
  const [usageMetric, setUsageMetric] = useState(initialSchedule?.usage_metric ?? '')
  const [intervalUsageAmount, setIntervalUsageAmount] = useState(
    String(initialSchedule?.interval_usage_amount ?? ''),
  )
  const [startingUsageValue, setStartingUsageValue] = useState('')

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const base = { entity_id: entityId, title, planned_time: time || null }

    if (mode === 'once') {
      onSubmit({ ...base, interval_type: 'once', planned_at: date })
    } else if (mode === 'days') {
      onSubmit({ ...base, interval_type: 'time', interval_days: Number(intervalDays), starting_at: date || null })
    } else if (mode === 'monthly_day') {
      onSubmit({ ...base, interval_type: 'monthly', monthly_day: Number(monthlyDay), starting_at: date || null })
    } else if (mode === 'monthly_weekday') {
      onSubmit({
        ...base,
        interval_type: 'monthly',
        monthly_weekday: Number(monthlyWeekday),
        monthly_week_index: Number(monthlyWeekIndex),
        starting_at: date || null,
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
        <span className="text-sm">{copy.titleLabel}</span>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={copy.titlePlaceholder}
          className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
        />
      </label>

      {isPlan ? (
        <>
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
                  <input
                    type="checkbox"
                    checked={recurring}
                    onChange={(e) => setMode(e.target.checked ? 'days' : 'once')}
                  />
                  Recurring
                </label>
              )}
            </div>
          )}

          {recurring && !isEdit && (
            <label className="block">
              <span className="text-sm">Repeats</span>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as RecurrenceMode)}
                className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
              >
                <option value="days">Every N days</option>
                <option value="monthly_day">Monthly, on a specific day</option>
                <option value="monthly_weekday">Monthly, on a specific weekday (e.g. 2nd Friday)</option>
              </select>
            </label>
          )}
        </>
      ) : (
        <>
          {isEdit ? (
            <p className="text-sm text-subtle">
              {mode === 'usage' ? 'Usage-based schedule' : 'Time-based schedule'}
            </p>
          ) : (
            <label className="block">
              <span className="text-sm">Recurrence</span>
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value as RecurrenceMode)}
                className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
              >
                <option value="days">Time-based (e.g. every N days)</option>
                <option value="usage">Usage-based (e.g. every N miles)</option>
              </select>
            </label>
          )}
        </>
      )}

      {mode === 'days' && (
        <div className="grid grid-cols-2 gap-3">
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
          {!isPlan && !isEdit && (
            <label className="block">
              <span className="text-sm">Starting from</span>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
              />
            </label>
          )}
        </div>
      )}

      {mode === 'monthly_day' && (
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

      {mode === 'monthly_weekday' && (
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

      {mode === 'usage' && (
        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-sm">Usage metric key</span>
            <input
              required
              value={usageMetric}
              onChange={(e) => setUsageMetric(e.target.value)}
              placeholder="odometer_reading"
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
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
              className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
            />
          </label>
          {!isEdit && (
            <label className="block col-span-2">
              <span className="text-sm">Current reading (optional)</span>
              <input
                type="number"
                value={startingUsageValue}
                onChange={(e) => setStartingUsageValue(e.target.value)}
                placeholder="e.g. current odometer reading"
                className="mt-1 w-full rounded-md border border-line bg-transparent px-2 py-1.5"
              />
            </label>
          )}
        </div>
      )}

      {submitError && <p className="text-sm text-red-500">{submitError}</p>}

      <button
        type="submit"
        disabled={isSubmitting}
        className="rounded-md bg-primary text-white hover:bg-primary-hover px-4 py-2 font-medium disabled:opacity-50"
      >
        {isSubmitting ? 'Saving…' : (submitLabel ?? copy.submitLabel)}
      </button>
    </form>
  )
}
