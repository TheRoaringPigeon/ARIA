import type { Schedule } from '../api/types'

export const WEEKDAYS = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
  { value: 5, label: 'Saturday' },
  { value: 6, label: 'Sunday' },
]

export const WEEK_INDEXES = [
  { value: 1, label: '1st' },
  { value: 2, label: '2nd' },
  { value: 3, label: '3rd' },
  { value: 4, label: '4th' },
  { value: -1, label: 'Last' },
]

export type RecurrenceMode = 'days' | 'monthly_day' | 'monthly_weekday'

export function recurrenceModeOf(plan: Schedule | undefined): RecurrenceMode {
  if (plan?.interval_type === 'monthly') {
    return plan.monthly_day !== null ? 'monthly_day' : 'monthly_weekday'
  }
  return 'days'
}

export function describeRecurrence(plan: Schedule): string {
  if (plan.interval_type === 'time') {
    return `Recurring — every ${plan.interval_days} days`
  }
  if (plan.interval_type === 'monthly' && plan.monthly_day !== null) {
    return `Recurring — monthly on the ${plan.monthly_day}${ordinalSuffix(plan.monthly_day)}`
  }
  if (plan.interval_type === 'monthly') {
    const weekIndex = WEEK_INDEXES.find((w) => w.value === plan.monthly_week_index)?.label ?? ''
    const weekday = WEEKDAYS.find((w) => w.value === plan.monthly_weekday)?.label ?? ''
    return `Recurring — ${weekIndex} ${weekday} of the month`
  }
  return 'One-time'
}

function ordinalSuffix(day: number): string {
  if (day % 10 === 1 && day !== 11) return 'st'
  if (day % 10 === 2 && day !== 12) return 'nd'
  if (day % 10 === 3 && day !== 13) return 'rd'
  return 'th'
}
