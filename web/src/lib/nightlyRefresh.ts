// The nightly refresh of the user's own screener: its query key, the time zones a refresh
// time can be in, and keeping the server's stored keys in step with the browser's.
import { enableNightlyRefresh } from '../api/endpoints'
import type { NightlyRefreshStatus } from '../api/types'

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
    // The same keys and cap the user chose, so a changed key does not widen them.
    use_keys: status.providers ?? null,
    price_history_stocks:
      status.price_history_stocks === null
        ? ALL_STOCKS
        : (status.price_history_stocks ?? DEFAULT_STOCKS),
  })
}

/** "As many as the keys' plans allow": more stocks than are listed. */
export const ALL_STOCKS = 10_000

/** Stocks given price histories until the user chooses, as the server's default: under a
 * free Tiingo key's 50 requests an hour. */
export const DEFAULT_STOCKS = 40

/** Choices of how many stocks get price histories each night. */
export const PRICE_HISTORY_CHOICES = [
  { value: 0, label: 'None' },
  { value: 40, label: '40' },
  { value: 200, label: '200' },
  { value: ALL_STOCKS, label: 'All the plans allow' },
] as const

/** Price-history keys, in the order a refresh tries them. */
export const PRICE_KEYS = ['tiingo', 'fmp', 'eodhd', 'alpha_vantage'] as const
