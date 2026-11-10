import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Account, NightlyRefreshStatus } from '../api/types'
import { NightlyRefreshCard } from './NightlyRefreshCard'

const OFF: NightlyRefreshStatus = {
  available: true,
  enabled: false,
  providers: [],
  latest_run: null,
  last_success: null,
}

const ON: NightlyRefreshStatus = {
  ...OFF,
  enabled: true,
  refresh_time: '19:00',
  time_zone: 'America/Chicago',
  providers: ['finnhub', 'tiingo'],
  keys_updated_at: '2026-11-03T12:00:00Z',
  next_run_at: '2026-11-04T01:00:00Z',
  latest_run: {
    status: 'failed',
    started_at: '2026-11-03T01:00:00Z',
    finished_at: '2026-11-03T01:05:00Z',
    error: 'Finnhub rejected your API key; update it in Settings.',
  },
  last_success: {
    status: 'succeeded',
    started_at: '2026-11-02T01:00:00Z',
    finished_at: '2026-11-02T02:30:00Z',
    stocks: 3120,
    priced: 480,
    prices_as_of: '2026-10-30',
  },
}

const account = (finnhub: boolean) => ({ finnhub: { configured: finnhub } }) as unknown as Account

afterEach(() => vi.restoreAllMocks())

function renderCard(
  withFinnhub = true,
  props: Partial<Parameters<typeof NightlyRefreshCard>[0]> = {},
) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NightlyRefreshCard account={account(withFinnhub)} {...props} />
    </QueryClientProvider>,
  )
}

describe('NightlyRefreshCard', () => {
  it('opts in with the chosen time, and shows what is stored and how runs went', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async (_url, init) => new Response(JSON.stringify(init?.method === 'PUT' ? ON : OFF)),
      )
    renderCard()
    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })

    await userEvent.click(box)

    await waitFor(() => expect(box).toBeChecked())
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(put).toBeDefined()
    expect(JSON.parse(String(put![1]!.body))).toEqual({
      refresh_time: '19:00',
      time_zone: 'America/New_York',
      use_keys: ['finnhub'],
      price_history_limits: {},
      universes: ['us_stocks', 'us_etfs'],
      custom_symbols: [],
    })
    expect(screen.getByText(/Stored, encrypted: Finnhub, Tiingo/)).toBeInTheDocument()
    expect(screen.getByText(/Finnhub rejected your API key/)).toBeInTheDocument()
    expect(screen.getByText('3,120')).toBeInTheDocument()
    expect(screen.getByText('480')).toBeInTheDocument()
  })

  it('opts out, deleting the stored keys', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async (_url, init) => new Response(JSON.stringify(init?.method === 'DELETE' ? OFF : ON)),
      )
    renderCard()
    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })
    await waitFor(() => expect(box).toBeChecked())

    await userEvent.click(box)

    await waitFor(() => expect(box).not.toBeChecked())
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true)
  })

  it('cannot be turned on without a Finnhub key or a server that can store keys', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(OFF)))
    renderCard(false)

    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })
    await screen.findByText(/To turn this on, add a Finnhub key/)
    expect(box).toBeDisabled()
  })

  it('saves a time zone the user picks', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify(ON)))
    renderCard()
    const zone = await screen.findByRole('combobox', { name: 'Time zone' })
    await waitFor(() => expect(zone).toHaveValue('America/Chicago'))

    await userEvent.selectOptions(zone, 'America/Los_Angeles')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => {
      const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
      expect(JSON.parse(String(put![1]!.body))).toEqual({
        refresh_time: '19:00',
        time_zone: 'America/Los_Angeles',
        use_keys: ['finnhub'],
        price_history_limits: {},
        universes: ['us_stocks', 'us_etfs'],
        custom_symbols: [],
      })
    })
  })

  it('saves the sets and tickers chosen, and previews what they would use', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify(ON)))
    renderCard()
    const usStocks = await screen.findByRole('checkbox', { name: 'Every US stock' })
    await waitFor(() => expect(usStocks).toBeChecked())

    await userEvent.click(usStocks)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Every US ETF' }))
    await userEvent.click(screen.getByRole('checkbox', { name: 'S&P 500' }))
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'International stocks (US-listed)' }),
    )
    const add = screen.getByRole('combobox', { name: 'Add a stock or ETF to refresh' })
    await userEvent.type(add, 'spy{Enter}')
    await userEvent.type(add, '7203.T{Enter}')
    await userEvent.type(add, 'SPY{Enter}')
    await userEvent.click(screen.getByRole('button', { name: 'Remove 7203.T' }))

    await waitFor(() =>
      expect(
        fetch.mock.calls.some(
          ([url]) =>
            String(url).includes('universe=sp500') && String(url).includes('custom_symbol=SPY'),
        ),
      ).toBe(true),
    )
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => {
      const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
      expect(JSON.parse(String(put![1]!.body))).toMatchObject({
        universes: ['sp500', 'international'],
        custom_symbols: ['SPY'],
      })
    })
  })

  it('will not save a refresh of nothing', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(ON)))
    renderCard()
    const usStocks = await screen.findByRole('checkbox', { name: 'Every US stock' })
    await waitFor(() => expect(usStocks).toBeChecked())

    await userEvent.click(usStocks)
    await userEvent.click(screen.getByRole('checkbox', { name: 'Every US ETF' }))

    expect(screen.getByText(/there is nothing to refresh/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeDisabled()
  })

  it('says what the latest refresh did in place of a ticker it could not refresh', async () => {
    const note =
      '7203.T: free plans have no figures for listings abroad, so its company’s US listing, TM, was refreshed instead.'
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ ...ON, latest_run: { ...ON.last_success, notes: [note] } })),
    )
    renderCard()

    expect(await screen.findByText(note)).toBeInTheDocument()
  })

  it('lets the user choose the keys and how many histories, and says what a refresh uses', async () => {
    const withTiingo = {
      finnhub: { configured: true },
      tiingo: { configured: true },
      eodhd: { configured: true },
    } as unknown as Account
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, init) => {
      const query = new URL(String(url), 'http://localhost').searchParams
      if (init?.method === 'PUT') return new Response(JSON.stringify(ON))
      if (query.has('use_key')) {
        const limits = Object.fromEntries(
          query.getAll('price_limit').map((item) => item.split(':') as [string, string]),
        )
        return new Response(
          JSON.stringify({
            ...OFF,
            key_use: [
              { provider: 'finnhub', requests: 5500, detail: '' },
              ...query
                .getAll('use_key')
                .filter((key) => key !== 'finnhub')
                .map((provider) => ({
                  provider,
                  requests: limits[provider] === 'all' ? 3000 : Number(limits[provider]),
                  detail: '',
                })),
            ],
          }),
        )
      }
      return new Response(JSON.stringify(OFF))
    })
    const usage = {
      providers: [
        {
          provider: 'tiingo',
          name: 'Tiingo',
          plan_detected: false,
          source: 'pyqxel',
          limits: [
            { window: 'hour', unit: 'requests', limit: 50, resets: '', source: 'pyqxel' },
            { window: 'day', unit: 'requests', limit: 1000, resets: '', source: 'pyqxel' },
            { window: 'month', unit: 'symbols', limit: 500, resets: '', source: 'pyqxel' },
          ],
        },
      ],
      stored_prices: { symbols: 0, histories: 0, bytes: 0 },
    } as Parameters<typeof NightlyRefreshCard>[0]['usage']
    renderCard(true, { account: withTiingo, usage })

    // Tiingo alone is used for prices until the user chooses, for its free plan's 50.
    expect(await screen.findByText(/about 5,500 requests/)).toBeInTheDocument()
    expect(
      await screen.findByText(/5% of its 1,000 a day, 100% of its 50 an hour/),
    ).toBeInTheDocument()
    expect(screen.getByText(/No daily limit/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'EODHD' })).not.toBeChecked()

    // EODHD gets its own limit, starting at its free plan's 20, apart from Tiingo's.
    await userEvent.click(screen.getByRole('checkbox', { name: 'EODHD' }))
    const eodhd = await screen.findByRole('group', {
      name: /stocks and etfs eodhd fetches price histories for/i,
    })
    expect(within(eodhd).getByRole('button', { name: '20 (free plan)' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await userEvent.click(within(eodhd).getByRole('button', { name: 'All the plan allows' }))
    expect(await screen.findByText(/about 3,000 requests/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('checkbox', { name: /refresh my own screener/i }))

    await waitFor(() => {
      const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
      expect(JSON.parse(String(put![1]!.body))).toMatchObject({
        use_keys: ['finnhub', 'tiingo', 'eodhd'],
        price_history_limits: { tiingo: 50, eodhd: null },
      })
    })
  })

  it('ticks and locks the sets a ticked set holds', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(OFF)))
    renderCard()

    const nasdaq100 = await screen.findByRole('checkbox', { name: /^Nasdaq-100/ })
    // Every US stock is ticked until the user chooses, and holds the Nasdaq-100.
    expect(nasdaq100).toBeChecked()
    expect(nasdaq100).toBeDisabled()

    await userEvent.click(screen.getByRole('checkbox', { name: /^Every US stock/ }))
    expect(nasdaq100).not.toBeChecked()
    expect(nasdaq100).toBeEnabled()

    await userEvent.click(screen.getByRole('checkbox', { name: /^Stocks listed on Nasdaq/ }))
    expect(nasdaq100).toBeChecked()
    expect(nasdaq100).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: /^S&P 500/ })).not.toBeChecked()
  })
})

describe('NightlyRefreshCard without a Finnhub key', () => {
  it('starts unchecked and explains why it cannot be turned on yet', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(OFF)))
    renderCard(false)
    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })

    await waitFor(() => expect(box).toBeDisabled())
    expect(box).not.toBeChecked()
    expect(box).toHaveAccessibleDescription(/add a Finnhub key/i)
  })

  it('never blocks turning it off, and says nothing about adding a key after', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(
        async (_url, init) => new Response(JSON.stringify(init?.method === 'DELETE' ? OFF : ON)),
      )
    renderCard(false)
    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })
    await waitFor(() => expect(box).toBeChecked())
    expect(box).toBeEnabled()
    expect(screen.getByText(/You can turn it off here at any time/)).toBeInTheDocument()

    await userEvent.click(box)

    await waitFor(() => expect(box).not.toBeChecked())
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true)
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('clears an earlier failure to turn it on once it is turned off', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
      if (init?.method === 'PUT') {
        return new Response(JSON.stringify({ detail: 'Add your Finnhub key first.' }), {
          status: 422,
        })
      }
      return new Response(JSON.stringify(init?.method === 'DELETE' ? OFF : ON))
    })
    renderCard(true)
    const box = await screen.findByRole('checkbox', { name: /refresh my own screener/i })
    await waitFor(() => expect(box).toBeChecked())
    // A failed save leaves an error, which turning the refresh off then clears.
    await userEvent.clear(screen.getByLabelText('Start at'))
    await userEvent.type(screen.getByLabelText('Start at'), '20:00')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText(/Add your Finnhub key first/)).toBeInTheDocument()

    await userEvent.click(box)

    await waitFor(() =>
      expect(screen.queryByText(/Add your Finnhub key first/)).not.toBeInTheDocument(),
    )
  })
})
