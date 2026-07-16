import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDueSoon } from '../hooks/useSchedules'

export function DueSoonPage() {
  const [withinDays, setWithinDays] = useState(30)
  const dueQuery = useDueSoon(withinDays)

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">What's due</h1>
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
      </div>
      <p className="mt-1 text-sm text-subtle">
        Usage-based schedules aren't shown here yet — there's no reliable current-reading source
        for them in this milestone. They're still visible on each entity's Schedules tab.
      </p>

      <div className="mt-4 grid gap-2">
        {dueQuery.isPending && <p className="text-subtle">Loading…</p>}
        {dueQuery.data?.length === 0 && (
          <p className="text-subtle">Nothing due in this window.</p>
        )}
        {dueQuery.data?.map((item) => (
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
              {item.is_overdue ? 'Overdue' : 'Due'} {item.schedule.next_due_at}
              {item.schedule.planned_time ? ` at ${item.schedule.planned_time}` : ''}
            </span>
          </Link>
        ))}
      </div>
    </div>
  )
}
