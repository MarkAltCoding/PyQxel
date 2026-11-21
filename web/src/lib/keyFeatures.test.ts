import { afterEach, describe, expect, it, vi } from 'vitest'
import { send } from '../api/client'
import { featureOf, limitsHeader, storeKeyRule } from './keyFeatures'
import { clearProviderKeys, setProviderKey } from './providerKeys'

afterEach(() => {
  vi.restoreAllMocks()
  clearProviderKeys()
})

describe('key rules', () => {
  it('names the part of the app a request is for', () => {
    expect(featureOf('/api/v1/stocks/AAPL/history', '/stocks/AAPL')).toBe('stock')
    expect(featureOf('/api/v1/stocks/AAPL/history', '/backtest')).toBe('backtest')
    expect(featureOf('/api/v1/picks/stream', '/portfolio')).toBe('picks')
    expect(featureOf('/api/v1/search', '/markets')).toBe('search')
    expect(featureOf('/api/v1/usage', '/settings')).toBeNull()
  })

  it('leaves out a key turned off, sends the limits, and says why in an error', async () => {
    setProviderKey('fmp', 'fmp-key-12345')
    setProviderKey('finnhub', 'finnhub-key-123')
    storeKeyRule('fmp', 'markets', { off: true })
    storeKeyRule('finnhub', 'markets', { daily: 50 })
    expect(limitsHeader('markets')).toBe('finnhub=50')
    window.history.pushState({}, '', '/markets')
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"detail":"Market news needs a Finnhub key."}', {
        status: 403,
        headers: { 'X-PyQxel-Held-Back': 'finnhub' },
      }),
    )

    await expect(send('/api/v1/markets/news')).rejects.toThrow(
      /turned off for the Markets page in Settings.*made 50 requests today for the Markets page/,
    )
    const headers = fetch.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers['X-FMP-Key']).toBeUndefined()
    expect(headers['X-Finnhub-Key']).toBe('finnhub-key-123')
    expect(headers['X-PyQxel-Feature']).toBe('markets')
    expect(headers['X-PyQxel-Key-Limits']).toBe('finnhub=50')
    window.history.pushState({}, '', '/')
  })
})
