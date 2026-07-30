import { useMemo, useState } from 'react'
import type { CalendarOccurrence } from '../api/types'
import type { EntityDomain } from '../domains'
import { useHousehold } from '../hooks/useHousehold'
import { useScheduleCalendar } from '../hooks/useSchedules'
import { addMonths, buildMonthGrid, startOfMonth, toISODate, todayInTimezone } from '../lib/dates'
import { DayComposer } from './DayComposer'

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const MAX_CHIPS_PER_DAY = 3

interface Props {
  domain?: EntityDomain
}

export function CalendarView({ domain }: Props) {
  const [monthCursor, setMonthCursor] = useState(() => startOfMonth(new Date()))
  const [composerDate, setComposerDate] = useState<string | null>(null)

  const grid = useMemo(() => buildMonthGrid(monthCursor), [monthCursor])
  const from = toISODate(grid[0])
  const to = toISODate(grid[grid.length - 1])
  const calendarQuery = useScheduleCalendar(from, to, domain)

  const byDate = useMemo(() => {
    const map = new Map<string, CalendarOccurrence[]>()
    for (const occ of calendarQuery.data ?? []) {
      const list = map.get(occ.occurrence_date)
      if (list) list.push(occ)
      else map.set(occ.occurrence_date, [occ])
    }
    return map
  }, [calendarQuery.data])

  const { data: household } = useHousehold()
  const todayStr = todayInTimezone(household?.timezone)

  return (
    <div className="mt-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMonthCursor((m) => addMonths(m, -1))}
            className="rounded-md border border-line px-2 py-1 text-sm"
          >
            ← Prev
          </button>
          <button
            type="button"
            onClick={() => setMonthCursor(startOfMonth(new Date()))}
            className="rounded-md border border-line px-2 py-1 text-sm"
          >
            Today
          </button>
          <button
            type="button"
            onClick={() => setMonthCursor((m) => addMonths(m, 1))}
            className="rounded-md border border-line px-2 py-1 text-sm"
          >
            Next →
          </button>
        </div>
        <h2 className="text-lg font-semibold">
          {monthCursor.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })}
        </h2>
      </div>

      {calendarQuery.isPending && <p className="mt-3 text-subtle">Loading…</p>}

      <div className="mt-3 grid grid-cols-7 gap-1">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="px-1 pb-1 text-center text-xs font-medium text-subtle">
            {label}
          </div>
        ))}
        {grid.map((day) => {
          const dateStr = toISODate(day)
          const inCurrentMonth = day.getMonth() === monthCursor.getMonth()
          const occurrences = byDate.get(dateStr) ?? []
          return (
            <button
              key={dateStr}
              type="button"
              onClick={() => setComposerDate(dateStr)}
              className={`min-h-20 rounded-md border border-divider p-1 text-left align-top hover:bg-surface-hover ${
                inCurrentMonth ? '' : 'opacity-40'
              } ${dateStr === todayStr ? 'border-primary' : ''}`}
            >
              <div className="text-xs text-subtle">{day.getDate()}</div>
              <div className="mt-1 space-y-0.5">
                {occurrences.slice(0, MAX_CHIPS_PER_DAY).map((occ) => {
                  const isOverdue = occ.is_next_due && occ.occurrence_date < todayStr
                  return (
                    <div
                      key={`${occ.schedule_id}-${occ.occurrence_date}`}
                      className={`truncate rounded px-1 py-0.5 text-xs ${
                        isOverdue
                          ? 'bg-red-500/15 text-red-600'
                          : occ.is_next_due
                            ? 'bg-amber-500/15 text-amber-700'
                            : 'bg-active'
                      }`}
                      title={`${occ.title} · ${occ.entity_name}${isOverdue ? ' · overdue' : occ.is_next_due ? ' · next due' : ''}`}
                    >
                      {occ.title}
                    </div>
                  )
                })}
                {occurrences.length > MAX_CHIPS_PER_DAY && (
                  <div className="text-xs text-subtle">+{occurrences.length - MAX_CHIPS_PER_DAY} more</div>
                )}
              </div>
            </button>
          )
        })}
      </div>

      {composerDate && (
        <DayComposer
          date={composerDate}
          domain={domain}
          existingOccurrences={byDate.get(composerDate) ?? []}
          onClose={() => setComposerDate(null)}
        />
      )}
    </div>
  )
}
