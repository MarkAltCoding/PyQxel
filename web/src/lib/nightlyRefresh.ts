// The nightly refresh of the user's own screener: its query key, the time zones a refresh
// time can be in, and keeping the server's stored keys in step with the browser's.
import { enableNightlyRefresh } from '../api/endpoints'
import type { NightlyRefreshSettings, NightlyRefreshStatus, NightlyTimeSlot } from '../api/types'

export const NIGHTLY_QUERY_KEY = ['nightly-refresh'] as const

/** The time zone a refresh time is in until the user picks another: New York's, US
 * Eastern Time, in which US markets keep their hours. */
export const DEFAULT_TIME_ZONE = 'America/New_York'

/** US time zones, listed first with their everyday names. */
export const US_TIME_ZONES: { value: string; label: string }[] = [
  { value: 'America/New_York', label: 'Eastern Time (New York)' },
  { value: 'America/Chicago', label: 'Central Time (Chicago)' },
  { value: 'America/Denver', label: 'Mountain Time (Denver)' },
  { value: 'America/Phoenix', label: 'Mountain Time, no daylight saving (Phoenix)' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (Los Angeles)' },
  { value: 'America/Anchorage', label: 'Alaska Time (Anchorage)' },
  { value: 'Pacific/Honolulu', label: 'Hawaii Time (Honolulu)' },
]

/** Every other time zone the browser knows, by its IANA name, e.g. Europe/London; a short
 * list where the browser cannot list them. */
export function otherTimeZones(): string[] {
  const us = new Set(US_TIME_ZONES.map((zone) => zone.value))
  let all: string[]
  try {
    all = Intl.supportedValuesOf('timeZone')
  } catch {
    all = ['UTC', 'Europe/London', 'Europe/Paris', 'Asia/Tokyo', 'Australia/Sydney']
  }
  return all.filter((zone) => !us.has(zone))
}

/** `moment` as a date and time in `zone`, naming the zone, e.g. "Nov 3, 7:00 PM EST". */
export function inTimeZone(moment: string, zone: string): string {
  try {
    return new Date(moment).toLocaleString('en-US', {
      timeZone: zone,
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    })
  } catch {
    return new Date(moment).toLocaleString('en-US')
  }
}

/** Minutes past midnight of `moment` in `zone`. */
function minutesIn(moment: string, zone: string): number {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(moment))
  const part = (type: string) => Number(parts.find((found) => found.type === type)?.value ?? 0)
  return part('hour') * 60 + part('minute')
}

/** The server's hourly check that starts a refresh set for `time` (HH:MM) in `zone`: the
 * first check at or after it. Refreshes started by the same check share the server. */
export function slotFor(
  slots: NightlyTimeSlot[],
  time: string,
  zone: string,
): NightlyTimeSlot | undefined {
  const [hour = NaN, minute = NaN] = time.split(':').map(Number)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return undefined
  const chosen = hour * 60 + minute
  try {
    return slots.find((slot) => (minutesIn(slot.checks_at, zone) - chosen + 1440) % 1440 < 60)
  } catch {
    return undefined
  }
}

/** The check that starts the most other refreshes, the soonest of those tied; none while
 * no one else refreshes. */
export function busiestSlot(slots: NightlyTimeSlot[]): NightlyTimeSlot | undefined {
  return slots.reduce<NightlyTimeSlot | undefined>(
    (best, slot) => (slot.others > (best?.others ?? 0) ? slot : best),
    undefined,
  )
}

/** The span of refresh times one check starts, in `zone`, e.g. "6:08 to 7:07 PM": from
 * just after the check an hour before up to the check itself. */
export function slotSpan(slot: NightlyTimeSlot, zone: string): string {
  const clock = (moment: Date) =>
    moment.toLocaleTimeString('en-US', { timeZone: zone, hour: 'numeric', minute: '2-digit' })
  const end = new Date(slot.checks_at)
  const start = new Date(end.getTime() - 59 * 60_000)
  try {
    return `${clock(start)} to ${clock(end)}`
  } catch {
    return `${start.toISOString().slice(11, 16)} to ${end.toISOString().slice(11, 16)} UTC`
  }
}

/** A time zone's name for people: its everyday US name, or its IANA name with spaces. */
export function timeZoneLabel(zone: string): string {
  return US_TIME_ZONES.find((us) => us.value === zone)?.label ?? zone.replace(/_/g, ' ')
}

/** Sends the browser's current keys to the server again, if the user opted in, so the
 * stored copy matches them. Settings calls it after a key changes. */
export async function resyncNightlyKeys(
  status: NightlyRefreshStatus | undefined,
): Promise<NightlyRefreshStatus | undefined> {
  if (!status?.enabled || !status.refresh_time || !status.time_zone) return undefined
  return enableNightlyRefresh({
    refresh_time: status.refresh_time,
    time_zone: status.time_zone,
    // The same keys, limits, sets, and tickers the user chose, so a changed key does not
    // widen them.
    use_keys: status.providers ?? null,
    universes: status.universes ?? EVERYTHING,
    custom_symbols: status.custom_symbols ?? [],
    price_history_limits: status.price_history_limits ?? {},
  })
}

/** "As many as the key's plan allows", as the price-history controls hold it: more stocks
 * than are listed. Sent to the server as null. */
export const ALL_THE_PLAN_ALLOWS = 10_000

/** Price histories each key fetches a night until the user chooses, as the server's
 * default: what its free plan answers, Tiingo's in an hour and the others' in a day. */
export const FREE_PLAN_HISTORIES: Record<PriceKey, number> = {
  tiingo: 50,
  fmp: 250,
  eodhd: 20,
  alpha_vantage: 25,
}

/** The usual choices for each key's nightly price histories, from what its plans allow:
 * the free plan's allowance, what a paid plan comfortably covers, and all its plan allows.
 * Custom takes any other number. */
export const PRICE_LIMIT_CHOICES: Record<PriceKey, { value: number; label: string }[]> = {
  tiingo: [
    { value: 50, label: '50 (free plan)' },
    { value: 1_000, label: '1,000 (paid plan)' },
    { value: ALL_THE_PLAN_ALLOWS, label: 'All the plan allows' },
  ],
  fmp: [
    { value: 250, label: '250 (free plan)' },
    { value: 3_000, label: '3,000 (paid plan)' },
    { value: ALL_THE_PLAN_ALLOWS, label: 'All the plan allows' },
  ],
  eodhd: [
    { value: 20, label: '20 (free plan)' },
    { value: 1_000, label: '1,000 (paid plan)' },
    { value: ALL_THE_PLAN_ALLOWS, label: 'All the plan allows' },
  ],
  alpha_vantage: [
    { value: 25, label: '25 (free plan)' },
    { value: 500, label: '500 (premium plan)' },
    { value: ALL_THE_PLAN_ALLOWS, label: 'All the plan allows' },
  ],
}

/** A key's limit as the server stores it: null for all its plan allows. */
export function limitToServer(value: number): number | null {
  return value >= ALL_THE_PLAN_ALLOWS ? null : value
}

/** A key's limit as the controls hold it: the sentinel for all its plan allows. */
export function limitFromServer(value: number | null | undefined, key: PriceKey): number {
  if (value === undefined) return FREE_PLAN_HISTORIES[key]
  return value === null ? ALL_THE_PLAN_ALLOWS : value
}

/** The preview route's form of the limits: KEY:COUNT, or KEY:all. */
export function limitParams(limits: Partial<Record<PriceKey, number | null>>): string[] {
  return Object.entries(limits).map(([key, value]) => `${key}:${value === null ? 'all' : value}`)
}

/** Price-history keys, in the order a refresh tries them. */
export const PRICE_KEYS = ['tiingo', 'fmp', 'eodhd', 'alpha_vantage'] as const

export type PriceKey = (typeof PRICE_KEYS)[number]

export type NightlyUniverse = NonNullable<NightlyRefreshSettings['universes']>[number]

/** Every US stock and ETF: what a refresh rebuilds until the user chooses. */
export const EVERYTHING: NightlyUniverse[] = ['us_stocks', 'us_etfs']

/** The sets a refresh can rebuild, combined as the user chooses. */
export const UNIVERSE_CHOICES: { value: NightlyUniverse; label: string }[] = [
  { value: 'us_stocks', label: 'Every US stock' },
  { value: 'us_etfs', label: 'Every US ETF' },
  { value: 'nasdaq', label: 'Stocks listed on Nasdaq' },
  { value: 'sp500', label: 'S&P 500' },
  { value: 'nasdaq100', label: 'Nasdaq-100' },
  { value: 'international', label: 'International stocks (US-listed)' },
]

/** Most tickers a user may add, as the server allows. */
export const MAX_CUSTOM_SYMBOLS = 100

/** The sets each set holds entirely: every US stock holds those listed on Nasdaq, the S&P
 * 500's and Nasdaq-100's companies, and foreign companies' US listings; Nasdaq holds the
 * Nasdaq-100. Ticking a set ticks these too. */
export const SET_HOLDS: Partial<Record<NightlyUniverse, NightlyUniverse[]>> = {
  us_stocks: ['nasdaq', 'sp500', 'nasdaq100', 'international'],
  nasdaq: ['nasdaq100'],
}

/** The set among `chosen` that holds `value` entirely, if one does. */
export function heldBy(value: NightlyUniverse, chosen: NightlyUniverse[]): NightlyUniverse | null {
  return chosen.find((set) => SET_HOLDS[set]?.includes(value)) ?? null
}
