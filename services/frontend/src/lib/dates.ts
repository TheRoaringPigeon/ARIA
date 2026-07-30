// Local-calendar-day helpers for the "what's due" calendar view. Critical:
// every function here parses/builds dates from local Y/M/D components, never
// via `new Date(dateStr)` — that UTC-parses a bare `YYYY-MM-DD` and can shift
// a day in negative-UTC-offset zones. Mirrors DueSoonPage.tsx's daysUntil().

// "Today" in a given IANA timezone, as an ISO date — `Intl`'s `en-CA`
// locale formats dates as YYYY-MM-DD directly, so no manual zero-padding
// or component reassembly is needed. Falls back to browser-local when `tz`
// is unset (a household that hasn't configured one), matching this app's
// prior behavior for that case.
export function todayInTimezone(tz: string | null | undefined): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz || undefined }).format(new Date())
}

export function parseLocalDate(dateStr: string): Date {
  const [y, m, d] = dateStr.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function toISODate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}

export function addMonths(d: Date, delta: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + delta, 1)
}

// 42-cell (6-week) grid covering the full weeks that overlap `monthCursor`'s
// month, Monday-start — matches this app's existing weekday convention
// (lib/recurrence.ts's WEEKDAYS, monthly_weekday: 0=Monday).
export function buildMonthGrid(monthCursor: Date): Date[] {
  const firstOfMonth = startOfMonth(monthCursor)
  // getDay() is 0=Sunday..6=Saturday; convert to a Monday-start offset.
  const mondayOffset = (firstOfMonth.getDay() + 6) % 7
  const gridStart = new Date(firstOfMonth.getFullYear(), firstOfMonth.getMonth(), 1 - mondayOffset)

  const cells: Date[] = []
  for (let i = 0; i < 42; i++) {
    cells.push(new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i))
  }
  return cells
}
