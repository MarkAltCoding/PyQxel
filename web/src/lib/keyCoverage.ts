// Which of a user's market data keys cover each kind of data, and what adding another
// would unlock. The providers and their order mirror the server's (PROVIDER_ORDER in
// app/data/fetcher.py), which docs/data-sources.md explains: for each kind of data the
// server tries the user's keys cheapest first, a free plan before one that needs paying.

import type { Account } from '../api/types'

export type DataProvider = 'tiingo' | 'fmp' | 'eodhd' | 'alpha_vantage' | 'finnhub'

export const DATA_PROVIDER_NAMES: Record<DataProvider, string> = {
  tiingo: 'Tiingo',
  fmp: 'Financial Modeling Prep',
  eodhd: 'EODHD',
  alpha_vantage: 'Alpha Vantage',
  finnhub: 'Finnhub',
}

/** What one provider gives for one kind of data. */
interface Step {
  provider: DataProvider
  /** What its free plan gives, or null when it gives nothing. */
  free: string | null
  /** Whether the free plan alone covers this kind of data. */
  freeIsFull: boolean
  /** What a paid plan gives, and its price, where it adds something. */
  paid?: string
}

export interface DataFeature {
  id: string
  name: string
  /** The providers that can answer, in the order the server tries them. */
  steps: Step[]
}

export const DATA_FEATURES: DataFeature[] = [
  {
    id: 'history-us',
    name: 'Price history of US stocks, ETFs and funds',
    steps: [
      {
        provider: 'tiingo',
        free: '30+ years, adjusted for splits and dividends; 500 different symbols a month',
        freeIsFull: true,
        paid: 'Power ($30 a month): every symbol, no monthly cap',
      },
      {
        provider: 'fmp',
        free: '5 years for a sample of large, popular companies only',
        freeIsFull: false,
        paid: 'Starter (about $29 a month): every US stock',
      },
      {
        provider: 'eodhd',
        free: 'the last year only, 20 requests a day',
        freeIsFull: false,
        paid: 'Historian ($19.99 a month): 30+ years',
      },
      {
        provider: 'alpha_vantage',
        free: 'the latest 100 days, not adjusted for splits or dividends',
        freeIsFull: false,
        paid: 'Premium (from $49.99 a month): full adjusted history',
      },
      {
        provider: 'finnhub',
        free: null,
        freeIsFull: false,
        paid: 'a market data plan (from about $50 a month): candles',
      },
    ],
  },
  {
    id: 'history-index',
    name: 'Index history, such as the S&P 500',
    steps: [
      {
        provider: 'eodhd',
        free: 'the last year only, 20 requests a day',
        freeIsFull: false,
        paid: 'Historian ($19.99 a month): back to the 1920s for the S&P 500',
      },
      {
        provider: 'fmp',
        free: null,
        freeIsFull: false,
        paid: 'Starter or above (about $29 a month)',
      },
    ],
  },
  {
    id: 'history-international',
    name: 'Listings outside the US',
    steps: [
      {
        provider: 'eodhd',
        free: 'the last year in Europe, Canada, Latin America, Korea, Taiwan, China, Australia and South Africa (not Tokyo, Hong Kong, India, Milan or Singapore), 20 requests a day',
        freeIsFull: false,
        paid: 'Historian ($19.99 a month): 30+ years on the same exchanges',
      },
      {
        provider: 'alpha_vantage',
        free: 'the latest 100 days, not adjusted, for London, XETRA, Lisbon, Toronto, India (BSE), Shanghai and Shenzhen',
        freeIsFull: false,
        paid: 'Premium (from $49.99 a month): full adjusted history',
      },
      {
        provider: 'fmp',
        free: null,
        freeIsFull: false,
        paid: 'Premium (UK and Canada) or Ultimate (worldwide), about $69 and $139 a month',
      },
      {
        provider: 'finnhub',
        free: null,
        freeIsFull: false,
        paid: 'a global market data plan',
      },
    ],
  },
  {
    id: 'intraday',
    name: 'Intraday charts (1 to 90 minute bars)',
    steps: [
      {
        provider: 'tiingo',
        free: 'US bars from the IEX exchange; volume counts IEX trades only',
        freeIsFull: true,
      },
      {
        provider: 'eodhd',
        free: null,
        freeIsFull: false,
        paid: 'Active trader ($29.99 a month): 1-minute US bars since 2004, full volume',
      },
      { provider: 'fmp', free: null, freeIsFull: false, paid: 'a paid plan' },
      {
        provider: 'alpha_vantage',
        free: null,
        freeIsFull: false,
        paid: 'Premium (from $49.99 a month)',
      },
      { provider: 'finnhub', free: null, freeIsFull: false, paid: 'a market data plan' },
    ],
  },
  {
    id: 'quotes',
    name: 'Live quotes',
    steps: [
      { provider: 'finnhub', free: 'real time, US listings', freeIsFull: true },
      { provider: 'tiingo', free: 'US listings', freeIsFull: true },
      {
        provider: 'fmp',
        free: 'a sample of large companies, and indices',
        freeIsFull: false,
        paid: 'Starter (about $29 a month): every US stock',
      },
      {
        provider: 'eodhd',
        free: 'any exchange, 15 to 20 minutes late, 20 requests a day',
        freeIsFull: false,
        paid: 'Historian ($19.99 a month): no daily cap to speak of',
      },
      { provider: 'alpha_vantage', free: 'end-of-day prices', freeIsFull: false },
    ],
  },
  {
    id: 'profiles',
    name: 'Company profiles: sector, industry and market cap',
    steps: [
      {
        provider: 'fmp',
        free: 'all three for a sample of large companies, and ETFs',
        freeIsFull: false,
        paid: 'Starter (about $29 a month): every US stock',
      },
      {
        provider: 'finnhub',
        free: 'industry and market cap for any US stock',
        freeIsFull: true,
      },
      {
        provider: 'tiingo',
        free: 'names for any US stock, ETF or fund, and which of the three it is; sectors for the Dow 30',
        freeIsFull: false,
      },
      {
        provider: 'alpha_vantage',
        free: null,
        freeIsFull: false,
        paid: 'Premium (from $49.99 a month)',
      },
      {
        provider: 'eodhd',
        free: null,
        freeIsFull: false,
        paid: 'Fundamentals ($59.99 a month): worldwide',
      },
    ],
  },
  {
    id: 'funds',
    name: 'Fund holdings and costs: expense ratio, net assets, sectors and top holdings',
    steps: [
      {
        provider: 'alpha_vantage',
        free: 'ETFs and funds, one of its 25 requests a day each, kept for a day',
        freeIsFull: true,
      },
    ],
  },
]

/** Whether the request carried ``provider``'s key; older servers leave new ones out. */
export function hasKey(account: Account, provider: DataProvider): boolean {
  return Boolean(account[provider]?.configured)
}

export interface Coverage {
  /** ``full`` when a key's free plan covers it, ``partial`` when keys cover part of it on
   * free plans, ``none`` when no key does, and ``fallback`` when the server fills it in. */
  level: 'full' | 'partial' | 'none' | 'fallback'
  /** The user's keys that answer, in the order they are tried. */
  from: Step[]
  /** The cheapest key the user has not added that would add something, if any. */
  next: Step | null
}

/** How ``account``'s keys cover ``feature``. A paid plan the user has is not known here, so
 * each key counts for what its free plan gives, and a paid plan's extra is named. */
export function coverage(feature: DataFeature, account: Account): Coverage {
  const from = feature.steps.filter((step) => hasKey(account, step.provider))
  const full = from.some((step) => step.freeIsFull)
  const missing = feature.steps.filter((step) => !hasKey(account, step.provider))
  // When the user's keys fall short, the next key worth adding is the first free one that
  // covers the feature, else the first with a free plan at all, else the first paid one.
  const next = full
    ? null
    : (missing.find((step) => step.freeIsFull) ??
      missing.find((step) => step.free !== null) ??
      missing[0] ??
      null)
  const covered = from.some((step) => step.free !== null)
  const level = full
    ? 'full'
    : covered
      ? 'partial'
      : account.market_data_fallback
        ? 'fallback'
        : 'none'
  return { level, from, next }
}
