// Provider keys (Anthropic, Tiingo, Financial Modeling Prep, EODHD, Alpha Vantage, Finnhub) live only in
// this browser. PyQxel's server never stores them: every API request carries them in
// their own headers, and the server uses them for that request alone.

import { readStored, writeStored } from './storage'

export type ProviderName = 'anthropic' | 'tiingo' | 'fmp' | 'eodhd' | 'alpha_vantage' | 'finnhub'

export type ProviderKeys = Partial<Record<ProviderName, string>>

/** The header each provider's key is sent in, as the server reads them. */
export const PROVIDER_HEADERS: Record<ProviderName, string> = {
  anthropic: 'X-Anthropic-Key',
  tiingo: 'X-Tiingo-Key',
  fmp: 'X-FMP-Key',
  eodhd: 'X-EODHD-Key',
  alpha_vantage: 'X-Alpha-Vantage-Key',
  finnhub: 'X-Finnhub-Key',
}

const STORAGE_KEY = 'pyqxel.providerKeys'

// Kept in memory too, so keys still work for this page load when storage is blocked.
// Read on first use rather than when this module loads, since the native app loads its
// secure storage first.
let cached: ProviderKeys | null = null

function current(): ProviderKeys {
  cached ??= load()
  return cached
}

function load(): ProviderKeys {
  try {
    const parsed: unknown = JSON.parse(readStored(STORAGE_KEY) ?? '{}')
    if (!parsed || typeof parsed !== 'object') return {}
    const loaded: ProviderKeys = {}
    for (const name of Object.keys(PROVIDER_HEADERS) as ProviderName[]) {
      const value = (parsed as Record<string, unknown>)[name]
      if (typeof value === 'string' && value) loaded[name] = value
    }
    return loaded
  } catch {
    return {}
  }
}

/** The keys this browser holds. */
export function providerKeys(): ProviderKeys {
  return current()
}

/** Keeps `value` as `name`'s key in this browser, or forgets it when `null`. */
export function setProviderKey(name: ProviderName, value: string | null): void {
  const next = { ...current() }
  if (value) next[name] = value
  else delete next[name]
  cached = next
  writeStored(STORAGE_KEY, Object.keys(next).length ? JSON.stringify(next) : null)
}

/** Forgets every key, as on signing out, so the next person here cannot use them. */
export function clearProviderKeys(): void {
  cached = {}
  writeStored(STORAGE_KEY, null)
}

/** The headers that send this browser's keys with a request. */
export function providerKeyHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  for (const [name, value] of Object.entries(current()) as [ProviderName, string][]) {
    headers[PROVIDER_HEADERS[name]] = value
  }
  return headers
}
