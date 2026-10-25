import { describe, expect, it } from 'vitest'
import type { MarketCalendar } from '../api/types'
import { calendarRows } from './calendar'

const CALENDAR: MarketCalendar = {
  start: '2026-10-25',
  end: '2026-11-23',
  fomc: [{ start: '2026-10-27', end: '2026-10-28', projections: false }],
  earnings: [
    { symbol: 'SMALL', date: '2026-10-28', market_cap: 2e10 },
    { symbol: 'AAPL', name: 'Apple Inc.', date: '2026-10-28', hour: 'amc', market_cap: 3e12 },
  ],
  ipos: [{ date: '2026-10-26', name: 'Big Co', symbol: 'BIG', status: 'expected' }],
  holidays: [{ date: '2026-11-26', name: 'Thanksgiving Day', trading_hours: null }],
  status: null,
  notice: null,
}

describe('calendarRows', () => {
  it('orders days, and within a day the Fed first, then larger companies', () => {
    const rows = calendarRows(CALENDAR)
    expect(rows.map((row) => [row.date, row.kind, row.symbol ?? null])).toEqual([
      ['2026-10-26', 'ipo', 'BIG'],
      ['2026-10-27', 'fomc', null],
      ['2026-10-28', 'fomc', null],
      ['2026-10-28', 'earnings', 'AAPL'],
      ['2026-10-28', 'earnings', 'SMALL'],
      ['2026-11-26', 'holiday', null],
    ])
    expect(rows[2]?.title).toBe('FOMC rate decision')
    expect(rows[3]?.title).toBe('Apple Inc. (AAPL)')
    expect(rows[3]?.detail).toBe('after the close')
    expect(rows[5]?.title).toBe('Thanksgiving Day: markets closed')
  })
})
