import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarView } from '../components/CalendarView'
import { DOMAIN_REGISTRY, DOMAINS, type EntityDomain } from '../domains'
import { useHousehold } from '../hooks/useHousehold'
import { useDueSoon } from '../hooks/useSchedules'
import { parseLocalDate, todayInTimezone } from '../lib/dates'

type View = 'list' | 'calendar'

const DOMAIN_FILTERS: Array<{ label: string; value: EntityDomain | undefined }> = [
  { label: 'All', value: undefined },
  ...DOMAINS.map((d) => ({ label: DOMAIN_REGISTRY[d].label, value: d })),
]

function daysUntil(dateStr: string, todayStr: string): number {
  const due = parseLocalDate(dateStr)
  const today = parseLocalDate(todayStr)
  return Math.round((due.getTime() - today.getTime()) / 86_400_000)
}

function weekdayName(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US', { weekday: 'long' })
}

function formatDueLabel(
  nextDueAt: string,
  plannedTime: string | null,
  isOverdue: boolean,
  todayStr: string
): string {
  const timeSuffix = plannedTime ? ` at ${plannedTime}` : ''
  if (!isOverdue && daysUntil(nextDueAt, todayStr) < 7) {
    return weekdayName(nextDueAt)
  }
  return `${isOverdue ? 'Overdue' : 'Due'} ${nextDueAt}${timeSuffix}`
}

export function DueSoonPage() {
  const [view, setView] = useState<View>('list')
  const [withinDays, setWithinDays] = useState(30)
  const [domain, setDomain] = useState<EntityDomain | undefined>(undefined)
  const [overdueOnly, setOverdueOnly] = useState(false)
  const dueQuery = useDueSoon(withinDays, domain)
  const { data: household } = useHousehold()
  const todayStr = todayInTimezone(household?.timezone)

  const items = overdueOnly ? dueQuery.data?.filter((item) => item.is_overdue) : dueQuery.data

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">What's due</h1>
        <div className="flex items-center gap-3">
          {view === 'list' && (
            <label className="flex items-center gap-2 text-sm text-subtle">
              Within
              <select
                value={withinDays}
                onChange={(e) => setWithinDays(Number(e.target.value))}
                className="rounded-md border border-line bg-transparent px-2 py-1"
              >
                <option value={7}>7 days</option>
                <option value={30}>30 days</option>
                <option value={90}>90 days</option>
                <option value={365}>1 year</option>
              </select>
            </label>
          )}
          <div className="flex rounded-md border border-line text-sm overflow-hidden">
            <button
              type="button"
              onClick={() => setView('list')}
              className={`px-3 py-1 ${view === 'list' ? 'bg-active' : 'hover:bg-surface-hover'}`}
            >
              List
            </button>
            <button
              type="button"
              onClick={() => setView('calendar')}
              className={`px-3 py-1 ${view === 'calendar' ? 'bg-active' : 'hover:bg-surface-hover'}`}
            >
              Calendar
            </button>
          </div>
        </div>
      </div>
      <p className="mt-1 text-sm text-subtle">
        Usage-based schedules aren't shown here yet — there's no reliable current-reading source
        for them in this milestone. They're still visible on each entity's Schedules tab.
      </p>

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
        {view === 'list' && (
          <label className="ml-auto flex items-center gap-2 text-sm text-subtle">
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(e) => setOverdueOnly(e.target.checked)}
            />
            Overdue only
          </label>
        )}
      </div>

      {view === 'calendar' ? (
        <CalendarView domain={domain} />
      ) : (
        <div className="mt-4 grid gap-2">
          {dueQuery.isPending && <p className="text-subtle">Loading…</p>}
          {dueQuery.isSuccess && items?.length === 0 && (
            <p className="text-subtle">
              {overdueOnly ? 'Nothing overdue.' : 'Nothing due in this window.'}
            </p>
          )}
          {items?.map((item) => (
            <Link
              key={item.schedule.id}
              to={`/entities/${item.schedule.entity_id}`}
              className="rounded-lg border border-divider p-3 flex items-center justify-between hover:bg-surface-hover"
            >
              <div>
                <p className="font-medium">{item.schedule.title}</p>
                <p className="text-sm text-subtle">{item.entity_name}</p>
              </div>
              <span className={`text-sm font-medium ${item.is_overdue ? 'text-red-500' : 'text-amber-600'}`}>
                {item.schedule.next_due_at
                  ? formatDueLabel(
                      item.schedule.next_due_at,
                      item.schedule.planned_time,
                      item.is_overdue,
                      todayStr
                    )
                  : null}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
