import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useDueSoon } from '../hooks/useSchedules'

const DISMISSED_DATE_KEY = 'aria-overdue-dismissed-date'

// UTC, not local — matches the day boundary the server uses for
// `is_overdue` (date.today() in a UTC container), so dismissing "today"
// stays in sync with the server across timezones.
function todayUTCDateString(): string {
  return new Date().toISOString().slice(0, 10)
}

export function OverdueBanner() {
  const { data: dueSoon } = useDueSoon()
  const [dismissedDate, setDismissedDate] = useState(() => localStorage.getItem(DISMISSED_DATE_KEY))

  const overdueCount = dueSoon?.filter((item) => item.is_overdue).length ?? 0
  if (overdueCount === 0) return null
  if (dismissedDate === todayUTCDateString()) return null

  const dismiss = () => {
    const today = todayUTCDateString()
    localStorage.setItem(DISMISSED_DATE_KEY, today)
    setDismissedDate(today)
  }

  return (
    <div className="border-b border-red-500/30 bg-red-500/10 px-6 py-2 flex items-center justify-center gap-3 text-sm text-red-700 dark:text-red-400">
      <Link to="/due-soon" className="hover:underline">
        You have {overdueCount} item{overdueCount === 1 ? '' : 's'} overdue.
      </Link>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss"
        className="text-red-700 dark:text-red-400 hover:opacity-70"
      >
        ×
      </button>
    </div>
  )
}
