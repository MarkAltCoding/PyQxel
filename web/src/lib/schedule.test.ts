import { describe, expect, it } from 'vitest'
import { ordinal, pickerMonths, scheduleLabel } from './schedule'

describe('schedule choices', () => {
  it('writes ordinals and describes a schedule', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 31].map(ordinal)).toEqual([
      '1st',
      '2nd',
      '3rd',
      '4th',
      '11th',
      '12th',
      '13th',
      '21st',
      '22nd',
      '31st',
    ])
    expect(scheduleLabel({ frequency: 'weekly', weekday: 6 }, '09:00')).toBe(
      'Every Sunday at 09:00',
    )
    expect(scheduleLabel({ frequency: 'monthly', month_day: -1 }, '09:00')).toBe(
      'The last day of each month at 09:00',
    )
  })

  it('offers this month and the next, weeks starting on Monday', () => {
    const [november, december] = pickerMonths(new Date(2026, 10, 20))
    expect(november?.title).toBe('November 2026')
    // November 1, 2026 is a Sunday: six blanks before it.
    expect(november?.days.slice(0, 7).map((d) => d?.getDate() ?? null)).toEqual([
      null,
      null,
      null,
      null,
      null,
      null,
      1,
    ])
    expect(december?.days.filter(Boolean)).toHaveLength(31)
  })
})
