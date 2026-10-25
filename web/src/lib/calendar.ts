// The market calendar as rows a day, for the Markets page.
import type { MarketCalendar } from '../api/types'
import { compact, number } from './format'

const HOURS: Record<string, string> = {
  bmo: 'before the open',
  amc: 'after the close',
  dmh: 'during market hours',
}

export interface CalendarRow {
  date: string
  kind: 'fomc' | 'earnings' | 'ipo' | 'holiday'
  title: string
  detail?: string
  symbol?: string
  /** Orders rows within a day: FOMC first, then larger companies and IPOs. */
  rank: number
}

/** Every event of the calendar as one row a day, in date order. */
export function calendarRows(calendar: MarketCalendar): CalendarRow[] {
  const rows: CalendarRow[] = []
  for (const meeting of calendar.fomc) {
    if (meeting.start !== meeting.end && meeting.start >= calendar.start) {
      rows.push({ date: meeting.start, kind: 'fomc', title: 'FOMC meeting begins', rank: 1e15 })
    }
    rows.push({
      date: meeting.end,
      kind: 'fomc',
      title: 'FOMC rate decision',
      detail: `Statement at 2 p.m. ET${meeting.projections ? ', with economic projections' : ''}`,
      rank: 1e16,
    })
  }
  for (const event of calendar.earnings) {
    const estimates = [
      event.hour ? HOURS[event.hour] : undefined,
      event.eps_estimate != null ? `EPS est. ${number(event.eps_estimate)}` : undefined,
      event.revenue_estimate != null
        ? `revenue est. ${compact(event.revenue_estimate)}`
        : undefined,
    ].filter(Boolean)
    rows.push({
      date: event.date,
      kind: 'earnings',
      symbol: event.symbol,
      title: event.name ? `${event.name} (${event.symbol})` : event.symbol,
      detail: estimates.join(' · ') || undefined,
      rank: event.market_cap ?? (event.revenue_estimate ?? 0) * 20,
    })
  }
  for (const ipo of calendar.ipos) {
    const parts = [
      ipo.status,
      ipo.exchange,
      ipo.shares != null ? `${compact(ipo.shares, '')} shares` : undefined,
      ipo.price ? `at $${ipo.price}` : undefined,
      ipo.value ? `${compact(ipo.value)} raised` : undefined,
    ].filter(Boolean)
    rows.push({
      date: ipo.date,
      kind: 'ipo',
      symbol: ipo.symbol ?? undefined,
      title: ipo.symbol ? `${ipo.name} (${ipo.symbol})` : ipo.name,
      detail: parts.join(' · ') || undefined,
      rank: ipo.value ?? 0,
    })
  }
  for (const holiday of calendar.holidays) {
    rows.push({
      date: holiday.date,
      kind: 'holiday',
      title: holiday.trading_hours
        ? `${holiday.name}: early close`
        : `${holiday.name}: markets closed`,
      detail: holiday.trading_hours ? `Trading ${holiday.trading_hours} ET` : undefined,
      rank: 1e14,
    })
  }
  return rows.sort((a, b) => a.date.localeCompare(b.date) || b.rank - a.rank)
}
