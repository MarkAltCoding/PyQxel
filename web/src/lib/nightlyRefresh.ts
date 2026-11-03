// The nightly refresh of the user's own screener: its query key, the time zone a new
// refresh time is in, and keeping the server's stored keys in step with the browser's.
import { enableNightlyRefresh } from '../api/endpoints'
import type { NightlyRefreshStatus } from '../api/types'

export const NIGHTLY_QUERY_KEY = ['nightly-refresh'] as const

/** The browser's time zone, which a new refresh time is in, e.g. America/Chicago. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York'
}

/** Sends the browser's current keys to the server again, if the user opted in, so the
 * stored copy matches them. Settings calls it after a key changes. */
export async function resyncNightlyKeys(
  status: NightlyRefreshStatus | undefined,
): Promise<NightlyRefreshStatus | undefined> {
  if (!status?.enabled || !status.refresh_time || !status.time_zone) return undefined
  return enableNightlyRefresh({ refresh_time: status.refresh_time, time_zone: status.time_zone })
}
