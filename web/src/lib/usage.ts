// Plan usage: which plan each key is on, whether each page may load without asking first,
// and the words for limits, windows and resets.
import type { ProviderUsage, UsageLimit } from '../api/types'
import { readStored, writeStored } from './storage'

export const USAGE_QUERY_KEY = 'usage'

const PLANS_KEY = 'pyqxel.plans'

/** The plan the user says each key is on, by provider; a key not named is on its free plan. */
export function storedPlans(): Record<string, string> {
  try {
    const parsed: unknown = JSON.parse(readStored(PLANS_KEY) ?? '{}')
    if (!parsed || typeof parsed !== 'object') return {}
    return Object.fromEntries(
      Object.entries(parsed as Record<string, unknown>).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string',
      ),
    )
  } catch {
    return {}
  }
}

export function setStoredPlan(provider: string, plan: string | null): void {
  const next = { ...storedPlans() }
  if (plan) next[provider] = plan
  else delete next[provider]
  writeStored(PLANS_KEY, JSON.stringify(next))
}

/** The plans as the API takes them: `provider:plan`. */
export const planQuery = (plans: Record<string, string>): string[] =>
  Object.entries(plans).map(([provider, plan]) => `${provider}:${plan}`)

// Asking before a page loads.

/** Pages that ask before calling providers, with their names for people. */
export const ASKING_PAGES = {
  portfolio: 'Portfolio',
  stock: 'Stock pages',
  markets: 'Markets',
} as const
export type AskingPage = keyof typeof ASKING_PAGES

const LOAD_KEY = (page: AskingPage) => `pyqxel.loadWithoutAsking.${page}`

/** Whether the user chose to load `page` without being asked. */
export const loadsWithoutAsking = (page: AskingPage): boolean => readStored(LOAD_KEY(page)) === '1'

export function setLoadsWithoutAsking(page: AskingPage, value: boolean): void {
  writeStored(LOAD_KEY(page), value ? '1' : null)
}

/** What this tab already agreed to load, by page and what it showed, so going back to a
 * page does not ask again for the same thing. */
const agreed = new Set<string>()
export const agreedTo = (signature: string) => agreed.has(signature)
export const agree = (signature: string) => void agreed.add(signature)
/** For tests. */
export const forgetAgreements = () => agreed.clear()

// Words.

const WINDOWS: Record<UsageLimit['window'], string> = {
  second: 'a second',
  minute: 'a minute',
  hour: 'an hour',
  day: 'a day',
  month: 'a month',
}

const UNITS: Record<UsageLimit['unit'], string> = {
  requests: 'requests',
  calls: 'calls',
  symbols: 'different symbols',
  bytes: 'of data',
  tokens: 'tokens',
  input_tokens: 'input tokens',
  output_tokens: 'output tokens',
}

/** A size in bytes for people: 512 KB, 1.2 GB. */
export function bytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '–'
  const units = ['bytes', 'KB', 'MB', 'GB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return unit === 0 ? `${size} bytes` : `${size.toFixed(size < 10 ? 1 : 0)} ${units[unit]}`
}

/** An amount of a limit's unit: "1,000", or "1.0 GB" for bytes. */
export function amount(limit: UsageLimit, value: number | null | undefined): string {
  if (value === null || value === undefined) return '–'
  return limit.unit === 'bytes' ? bytes(value) : value.toLocaleString('en-US')
}

/** A limit in words: "50 requests an hour", "500 different symbols a month". */
export function limitLabel(limit: UsageLimit): string {
  const what = UNITS[limit.unit]
  if (limit.limit === null || limit.limit === undefined) return `${what} ${WINDOWS[limit.window]}`
  if (limit.unit === 'bytes') return `${bytes(limit.limit)} ${WINDOWS[limit.window]}`
  return `${limit.limit.toLocaleString('en-US')} ${what} ${WINDOWS[limit.window]}`
}

/** When a limit resets, as a time on this device's clock: "10:00 PM", or "Nov 6, 6:00 PM". */
export function resetTime(limit: UsageLimit): string {
  if (!limit.resets_at) return limit.resets
  const at = new Date(limit.resets_at)
  const time = at.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
  if (at.toDateString() === new Date().toDateString()) return time
  return `${at.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${time}`
}

/** The share of a limit left, from 0 to 1, or null when unknown. */
export function shareLeft(limit: UsageLimit | null | undefined): number | null {
  if (!limit?.limit || limit.remaining === null || limit.remaining === undefined) return null
  return Math.max(0, Math.min(1, limit.remaining / limit.limit))
}

/** The limit of `report` over `window` counting requests (or EODHD's calls), if any. */
export function requestLimit(
  report: ProviderUsage | undefined,
  window: UsageLimit['window'],
): UsageLimit | undefined {
  return report?.limits?.find(
    (limit) => limit.window === window && (limit.unit === 'requests' || limit.unit === 'calls'),
  )
}
