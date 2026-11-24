// Number and date formatting. Returns and volatilities are decimals (0.25 = 25%).

const DASH = '—'

const missing = (value: number | null | undefined): value is null | undefined =>
  value === null || value === undefined || !Number.isFinite(value)

export function percent(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return DASH
  const text = (value * 100).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
  return `${text}%`
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (missing(value)) return DASH
  const text = percent(value, digits)
  return value > 0 ? `+${text}` : text
}

export function number(value: number | null | undefined, digits = 2): string {
  if (missing(value)) return DASH
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** Currencies some exchanges quote in that are a hundredth of another: London's pence. */
const MINOR_UNITS: Record<string, string> = { GBX: 'p', GBp: 'p' }

export function money(value: number | null | undefined, currency = 'USD', digits = 2): string {
  if (missing(value)) return DASH
  const minor = MINOR_UNITS[currency]
  if (minor) return `${number(value, digits)}${minor}`
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency,
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

/** 1.2T, 340.5B, 12.0M: for market caps and volumes. */
export function compact(value: number | null | undefined, prefix = '$'): string {
  if (missing(value)) return DASH
  const units: [number, string][] = [
    [1e12, 'T'],
    [1e9, 'B'],
    [1e6, 'M'],
    [1e3, 'K'],
  ]
  for (const [size, unit] of units) {
    if (Math.abs(value) >= size) return `${prefix}${(value / size).toFixed(1)}${unit}`
  }
  return `${prefix}${value.toFixed(0)}`
}

/** The US markets' time zone. Every moment PyQxel shows is in Eastern Time, the clock
 * the exchanges keep, whatever this device's own time zone is, so a story that ran at 9
 * p.m. in New York is dated that day everywhere. */
export const MARKET_TIME_ZONE = 'America/New_York'

/** A calendar day ("2026-11-23") as "Nov 23, 2026"; a moment as its date in Eastern Time. */
export function shortDate(value: string | null | undefined): string {
  if (!value) return DASH
  const day = value.length === 10
  const date = new Date(day ? `${value}T12:00:00Z` : value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    timeZone: day ? 'UTC' : MARKET_TIME_ZONE,
  })
}

export function dateTime(value: string | null | undefined): string {
  if (!value) return DASH
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const text = date.toLocaleString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: MARKET_TIME_ZONE,
  })
  return `${text} ET`
}

/** A moment's time of day in Eastern Time, e.g. "4:05 PM ET". */
export function clockTime(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  if (Number.isNaN(date.getTime())) return String(value)
  return `${date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: MARKET_TIME_ZONE })} ET`
}

/** The calendar day of a moment in Eastern Time, as "2026-11-23". */
export function marketDay(value: string | Date): string {
  const date = typeof value === 'string' ? new Date(value) : value
  return date.toLocaleDateString('en-CA', { timeZone: MARKET_TIME_ZONE })
}

/** How long ago `value` was, e.g. "5 minutes ago", "3 hours ago", or "Oct 12" beyond a week. */
export function timeAgo(value: string | null | undefined, now: Date = new Date()): string {
  if (!value) return DASH
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const minutes = Math.max(0, Math.round((now.getTime() - date.getTime()) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? '' : 's'} ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hour${hours === 1 ? '' : 's'} ago`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days} day${days === 1 ? '' : 's'} ago`
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: MARKET_TIME_ZONE,
  })
}

/** "+" / "-" class names for values whose sign is good or bad news. */
export function tone(value: number | null | undefined): 'up' | 'down' | undefined {
  if (missing(value) || value === 0) return undefined
  return value > 0 ? 'up' : 'down'
}

/** `items` as an English list, with the serial comma from three items on, as in
 * "A, B, and C"; two items are joined by the conjunction alone. */
export function listed(items: readonly string[], conjunction: 'and' | 'or' = 'and'): string {
  if (items.length < 3) return items.join(` ${conjunction} `)
  return `${items.slice(0, -1).join(', ')}, ${conjunction} ${items.at(-1)}`
}

/** Exchange codes as data providers write them, by the name an investor knows. */
const EXCHANGE_NAMES: Record<string, string> = {
  NMS: 'Nasdaq',
  NGM: 'Nasdaq',
  NCM: 'Nasdaq',
  NAS: 'Nasdaq',
  NASDAQ: 'Nasdaq',
  NYQ: 'NYSE',
  NYSE: 'NYSE',
  PCX: 'NYSE Arca',
  ARCA: 'NYSE Arca',
  'NYSE ARCA': 'NYSE Arca',
  NYSEARCA: 'NYSE Arca',
  ASE: 'NYSE American',
  AMEX: 'NYSE American',
  'NYSE MKT': 'NYSE American',
  BTS: 'Cboe BZX',
  BATS: 'Cboe BZX',
  CBOE: 'Cboe',
  PNK: 'OTC',
  OTC: 'OTC',
  OTCMKTS: 'OTC',
}

/** An exchange's familiar name ("Nasdaq" for NMS, "NYSE Arca" for PCX), or the code given. */
export function exchangeName(code: string | null | undefined): string | null {
  if (!code) return null
  return EXCHANGE_NAMES[code.trim().toUpperCase()] ?? code
}
