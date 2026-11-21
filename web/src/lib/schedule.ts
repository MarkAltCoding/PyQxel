// Choices for when a scheduled refresh runs, the screener's or Claude's Picks', as the
// server's RefreshSchedule takes them: Monday is 0 and Sunday 6.

import type { RefreshSchedule } from '../api/types'

export type Frequency = RefreshSchedule['frequency']

export const WEEKDAYS = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
] as const

export const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: 'trading_days', label: 'After each NYSE trading day' },
  { value: 'nightly', label: 'Every night' },
  { value: 'weekdays', label: 'Weeknights, Monday to Friday' },
  { value: 'weekly', label: 'Once a week' },
  { value: 'monthly', label: 'Once a month' },
  { value: 'dates', label: 'On dates I choose' },
]

/** Days of the month a monthly run can be set to; -1 is the last day of every month. */
export const MONTH_DAYS: { value: number; label: string }[] = [
  { value: -1, label: 'The last day' },
  ...Array.from({ length: 31 }, (_, index) => ({
    value: index + 1,
    label:
      index + 1 > 28 ? `The ${ordinal(index + 1)} (or the last day)` : `The ${ordinal(index + 1)}`,
  })),
]

export function ordinal(day: number): string {
  const tens = day % 100
  if (tens >= 11 && tens <= 13) return `${day}th`
  const suffixes: Record<number, string> = { 1: 'st', 2: 'nd', 3: 'rd' }
  return `${day}${suffixes[day % 10] ?? 'th'}`
}

/** Once after each NYSE session, the server's default too. */
export const DEFAULT_SCHEDULE: RefreshSchedule = {
  frequency: 'trading_days',
  weekday: 6,
  month_day: -1,
  dates: [],
  times: {},
}

/** A day as ISO YYYY-MM-DD, in local time. */
export const isoDay = (day: Date): string =>
  `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`

/** The months a date picker offers: the rest of this month and all of the next, each as
 * its days, with null for the blanks before the first, weeks starting on Monday. */
export function pickerMonths(today: Date): { title: string; days: (Date | null)[] }[] {
  return [0, 1].map((offset) => {
    const first = new Date(today.getFullYear(), today.getMonth() + offset, 1)
    const count = new Date(first.getFullYear(), first.getMonth() + 1, 0).getDate()
    const blanks = (first.getDay() + 6) % 7
    return {
      title: first.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }),
      days: [
        ...Array.from({ length: blanks }, () => null),
        ...Array.from(
          { length: count },
          (_, index) => new Date(first.getFullYear(), first.getMonth(), index + 1),
        ),
      ],
    }
  })
}

/** The schedule in words, for a summary. */
export function scheduleLabel(schedule: RefreshSchedule, time: string): string {
  const at = `at ${time}`
  switch (schedule.frequency) {
    case 'weekly':
      return `Every ${WEEKDAYS[schedule.weekday ?? 6]} ${at}`
    case 'monthly':
      return `${MONTH_DAYS.find((d) => d.value === (schedule.month_day ?? -1))?.label ?? 'The last day'} of each month ${at}`
    case 'dates':
      return `On ${(schedule.dates ?? []).length} chosen days ${at}`
    default:
      return `${FREQUENCIES.find((f) => f.value === schedule.frequency)?.label ?? 'Every night'} ${at}`
  }
}
