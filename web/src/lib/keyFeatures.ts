// Which parts of the app may use each provider key, and how many requests a day each part
// may make on it, as the person sets them in Settings. Requests are tagged with the part of
// the app they are for, so the server counts each part's use of a key and leaves a key out
// once a part has made its day's share of requests on it.

import type { ProviderName } from './providerKeys'
import { readStored, writeStored } from './storage'

export type KeyFeature = 'stock' | 'markets' | 'portfolio' | 'backtest' | 'picks' | 'search'

const MARKET_KEYS: ProviderName[] = ['tiingo', 'fmp', 'eodhd', 'alpha_vantage', 'finnhub']
const EVERY_KEY: ProviderName[] = [...MARKET_KEYS, 'anthropic']

/** The parts of the app, and the keys each can use. */
export const KEY_FEATURES: { value: KeyFeature; label: string; uses: ProviderName[] }[] = [
  { value: 'stock', label: 'Stock pages and their Claude reports', uses: EVERY_KEY },
  { value: 'markets', label: 'Markets: news and the calendar', uses: MARKET_KEYS },
  { value: 'portfolio', label: 'Portfolio, its simulations, and its review', uses: EVERY_KEY },
  { value: 'backtest', label: 'Backtests and the coding assistant', uses: EVERY_KEY },
  { value: 'picks', label: 'Claude’s Picks, wherever it is opened', uses: EVERY_KEY },
  { value: 'search', label: 'Ticker search', uses: ['finnhub'] },
]

export const FEATURE_LABELS: Record<KeyFeature, string> = Object.fromEntries(
  KEY_FEATURES.map((feature) => [feature.value, feature.label]),
) as Record<KeyFeature, string>

/** Short names, for messages. */
const FEATURE_NAMES: Record<KeyFeature, string> = {
  stock: 'the Stock page',
  markets: 'the Markets page',
  portfolio: 'the Portfolio page',
  backtest: 'Backtest',
  picks: 'Claude’s Picks',
  search: 'ticker search',
}

/** One part of the app's use of one key: off, or a cap on its requests a day. */
export interface FeatureRule {
  off?: boolean
  daily?: number
}

export type KeyRules = Partial<Record<ProviderName, Partial<Record<KeyFeature, FeatureRule>>>>

const KEY = 'pyqxel.keyFeatures'

export function storedKeyRules(): KeyRules {
  try {
    const parsed: unknown = JSON.parse(readStored(KEY) ?? '{}')
    return parsed && typeof parsed === 'object' ? (parsed as KeyRules) : {}
  } catch {
    return {}
  }
}

/** Keeps `rule` for `feature`'s use of `provider`'s key; the default (on, no limit) is
 * stored as nothing. */
export function storeKeyRule(provider: ProviderName, feature: KeyFeature, rule: FeatureRule): void {
  const rules = storedKeyRules()
  const forKey = { ...rules[provider] }
  const daily = rule.daily !== undefined && rule.daily >= 0 ? Math.floor(rule.daily) : undefined
  if (!rule.off && daily === undefined) delete forKey[feature]
  else
    forKey[feature] = {
      ...(rule.off ? { off: true } : {}),
      ...(daily !== undefined ? { daily } : {}),
    }
  const next = { ...rules, [provider]: forKey }
  if (Object.keys(forKey).length === 0) delete next[provider]
  writeStored(KEY, Object.keys(next).length ? JSON.stringify(next) : null)
}

/** The part of the app a request to `path` is for: ticker search and Claude's Picks by
 * what is asked, the rest by the page it is asked from; `null` for Settings, the account,
 * and anything else, which no rule limits. */
export function featureOf(path: string, page: string): KeyFeature | null {
  const api = path.replace(/^\/api\/v1/, '')
  if (api.startsWith('/search')) return 'search'
  if (api.startsWith('/picks')) return 'picks'
  if (api.startsWith('/me') || api.startsWith('/usage')) return null
  if (page.startsWith('/stocks/')) return 'stock'
  if (page.startsWith('/markets')) return 'markets'
  if (page.startsWith('/portfolio')) return 'portfolio'
  if (page.startsWith('/backtest')) return 'backtest'
  if (page.startsWith('/picks')) return 'picks'
  return null
}

/** The keys `feature` may not use at all. */
export function keysOff(feature: KeyFeature | null): ProviderName[] {
  if (feature === null) return []
  const rules = storedKeyRules()
  return (Object.keys(rules) as ProviderName[]).filter((name) => rules[name]?.[feature]?.off)
}

/** The daily limits `feature` has on keys, as the server reads them: `fmp=100,finnhub=50`. */
export function limitsHeader(feature: KeyFeature | null): string | null {
  if (feature === null) return null
  const rules = storedKeyRules()
  const parts = (Object.keys(rules) as ProviderName[]).flatMap((name) => {
    const daily = rules[name]?.[feature]?.daily
    return daily !== undefined ? [`${name}=${daily}`] : []
  })
  return parts.length ? parts.join(',') : null
}

const PROVIDER_NAMES: Record<ProviderName, string> = {
  anthropic: 'Anthropic',
  tiingo: 'Tiingo',
  fmp: 'Financial Modeling Prep',
  eodhd: 'EODHD',
  alpha_vantage: 'Alpha Vantage',
  finnhub: 'Finnhub',
}

/** Why keys were left out of a request that failed, to add to its error: those turned off
 * for its part of the app, and those whose daily limit there was reached. */
export function heldBackNote(
  feature: KeyFeature | null,
  off: ProviderName[],
  limited: ProviderName[],
): string | null {
  if (feature === null || (off.length === 0 && limited.length === 0)) return null
  const where = FEATURE_NAMES[feature]
  const rules = storedKeyRules()
  const notes = [
    ...off.map(
      (name) => `Your ${PROVIDER_NAMES[name]} key is turned off for ${where} in Settings.`,
    ),
    ...limited.map((name) => {
      const daily = rules[name]?.[feature]?.daily
      return `Your ${PROVIDER_NAMES[name]} key has made ${daily ?? 'its'} requests today for ${where}, the daily limit you set in Settings, so it was not used.`
    }),
  ]
  return notes.join(' ')
}
