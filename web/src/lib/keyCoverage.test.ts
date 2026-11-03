import { describe, expect, it } from 'vitest'
import type { Account } from '../api/types'
import { coverage, DATA_FEATURES, type DataProvider } from './keyCoverage'

const off = { configured: false, hint: null }
const on = { configured: true, hint: '…abcd' }

function account(keys: DataProvider[], fallback = false): Account {
  return {
    id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
    email: 'me@example.com',
    created_at: '2026-10-01T00:00:00Z',
    anthropic: off,
    fmp: keys.includes('fmp') ? on : off,
    alpha_vantage: keys.includes('alpha_vantage') ? on : off,
    finnhub: keys.includes('finnhub') ? on : off,
    tiingo: keys.includes('tiingo') ? on : off,
    eodhd: keys.includes('eodhd') ? on : off,
    limits: { ai_requests_per_hour: null, ai_requests_per_day: null },
    usage: { last_hour: 0, last_day: 0 },
    rate_limit_per_minute: 120,
    market_data_fallback: fallback,
    sec_filings: true,
  } as Account
}

const feature = (id: string) => DATA_FEATURES.find((f) => f.id === id)!

describe('market data coverage', () => {
  it('counts a free Tiingo key as full US history, and suggests it to anyone without', () => {
    expect(coverage(feature('history-us'), account(['tiingo'])).level).toBe('full')

    const fmpOnly = coverage(feature('history-us'), account(['fmp']))
    expect(fmpOnly.level).toBe('partial')
    expect(fmpOnly.from.map((step) => step.provider)).toEqual(['fmp'])
    expect(fmpOnly.next?.provider).toBe('tiingo')
  })

  it('has no free full source for index history, and suggests EODHD first', () => {
    const none = coverage(feature('history-index'), account(['tiingo', 'finnhub']))
    expect(none.level).toBe('none')
    expect(none.next?.provider).toBe('eodhd')

    expect(coverage(feature('history-index'), account(['eodhd'])).level).toBe('partial')
  })

  it('lists the keys that answer in the order they are tried', () => {
    const quotes = coverage(feature('quotes'), account(['eodhd', 'fmp', 'finnhub']))
    expect(quotes.from.map((step) => step.provider)).toEqual(['finnhub', 'fmp', 'eodhd'])
    expect(quotes.level).toBe('full')
    expect(quotes.next).toBeNull()
  })

  it('says when the server fills in what no key covers', () => {
    expect(coverage(feature('intraday'), account([], true)).level).toBe('fallback')
    expect(coverage(feature('intraday'), account([])).level).toBe('none')
  })

  it('names every provider of every feature, each once', () => {
    for (const each of DATA_FEATURES) {
      const providers = each.steps.map((step) => step.provider)
      expect(new Set(providers).size, each.id).toBe(providers.length)
      expect(
        each.steps.some((step) => step.free !== null || step.paid),
        each.id,
      ).toBe(true)
    }
  })
})
