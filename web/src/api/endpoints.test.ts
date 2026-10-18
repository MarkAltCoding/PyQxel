import { afterEach, describe, expect, it, vi } from 'vitest'
import { runCustomBacktest } from './endpoints'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('runCustomBacktest', () => {
  it('posts the positions and code to the symbol’s custom backtest route', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response('{}', { status: 200 }))
    const body = {
      code: 'def strategy(bars):\n    return bars["Close"] * 0 + 1',
      language: 'python' as const,
      timestamps: ['2026-10-15T00:00:00-04:00', '2026-10-16T00:00:00-04:00'],
      positions: [1, 0],
    }

    await runCustomBacktest('BRK.B', body)

    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/v1/stocks/BRK.B/backtest/custom')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(init?.body as string)).toEqual(body)
  })
})
