import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearProviderKeys, setProviderKey } from '../lib/providerKeys'
import { PicksPage } from './PicksPage'

const picks = (headline: string, overrides: Record<string, unknown> = {}) => ({
  id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
  report: {
    headline,
    market_view: 'Leadership is narrow.',
    approach: 'Weighted by conviction.',
    picks: [
      {
        symbol: 'NVDA',
        name: 'NVIDIA Corporation',
        kind: 'stock',
        conviction: 'high',
        weight: 0.6,
        thesis: 'Revenue grew 60% with wide margins.',
        risk: 'A slowdown in data center spending.',
        entry: 'Buy at market.',
        exit: 'Look again after earnings.',
      },
      {
        symbol: 'BND',
        name: 'Total Bond Market ETF',
        kind: 'etf',
        conviction: 'medium',
        weight: 0.4,
        thesis: 'Steadies the rest.',
        risk: 'Rising rates.',
        entry: 'Buy at market.',
        exit: 'Hold to the end of the period.',
      },
    ],
    passed_over: ['TSLA: priced for more than it delivers.'],
    considerations: ['Short-term gains are taxed as income.'],
    data_limitations: ['No news.'],
  },
  securities: [
    {
      symbol: 'NVDA',
      name: 'NVIDIA Corporation',
      exchange: 'Nasdaq',
      asset_type: 'stock',
      market_cap_source: 'provider',
      price: 140,
      market_cap: 3.4e12,
      avg_dollar_volume: 3e10,
      pe_ratio: 52.3,
      revenue_growth: 0.6,
      volatility: 0.48,
    },
  ],
  context: {
    profile: { horizon: { years: 1, months: 0, weeks: 0 }, amount: 10000, risk_tolerance: null },
    horizon: '1 year',
    horizon_weeks: 52.2,
    today: '2026-11-13',
    universe: { securities: 5200, own: true, prices_as_of: '2026-11-12', has_etfs: true },
    candidates: [{ symbol: 'NVDA' }, { symbol: 'TSLA' }],
    web_research: null,
  },
  model: 'claude-opus-5-5',
  generated_at: '2026-11-13T12:00:00Z',
  cached: true,
  disclaimer: 'No pick is a promise.',
  ...overrides,
})

type Route = (init: RequestInit | undefined, url: URL) => Response

/** Answers fetches by method and path; a path without a route is an empty 404. */
function serve(routes: Record<string, Route>) {
  const calls: { key: string; body: unknown }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    const key = `${init?.method ?? 'GET'} ${url.pathname}`
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const route = routes[key]
    return route ? route(init, url) : new Response('{"detail":"Not found."}', { status: 404 })
  })
  return calls
}

const json = (body: unknown) => new Response(JSON.stringify(body))
const STATUS = {
  'GET /api/v1/screener/status': () => json({ stocks: 5200, own: true }),
  'GET /api/v1/me/picks-schedule': () => json({ available: true, enabled: false }),
  'GET /api/v1/portfolio/portfolios': () => json({ portfolios: [] }),
  'GET /api/v1/picks/performance': () =>
    json({ benchmark: 'SPY', benchmark_name: 'S&P 500 (SPY)', items: [] }),
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PicksPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => setProviderKey('anthropic', 'sk-ant-test'))
afterEach(() => {
  vi.restoreAllMocks()
  clearProviderKeys()
})

describe('PicksPage', () => {
  it('shows the latest picks with their weights, reasons, and the screener’s figures', async () => {
    serve({
      ...STATUS,
      'GET /api/v1/picks/latest': () => json(picks('Quality with momentum.')),
      'GET /api/v1/picks': () => json({ items: [] }),
    })
    renderPage()

    expect(await screen.findByText('Quality with momentum.')).toBeInTheDocument()
    // The picks are the list's own items; each holds a smaller list of its figures.
    const [first, second] = Array.from(
      screen.getByRole('tabpanel').querySelectorAll<HTMLElement>('.pick-list > li'),
    )
    expect(within(first!).getByRole('link', { name: 'NVDA' })).toHaveAttribute(
      'href',
      '/stocks/NVDA',
    )
    expect(within(first!).getByText('high conviction')).toBeInTheDocument()
    expect(within(first!).getByText('60%')).toBeInTheDocument()
    expect(within(first!).getByText('· about $6,000')).toBeInTheDocument()
    expect(within(first!).getByText('Revenue grew 60% with wide margins.')).toBeInTheDocument()
    expect(within(first!).getByText('52.3')).toBeInTheDocument()
    expect(within(first!).getByText('+60.0%')).toBeInTheDocument()
    // A pick outside the screener has no figures, and says why.
    expect(within(second!).getByText(/Not in your screener/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Show in the screener' })).toHaveAttribute(
      'href',
      '/screener?picks=latest',
    )
    expect(screen.getByText(/stored, not billed again/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('tab', { name: /Passed over/ }))
    expect(screen.getByText('TSLA: priced for more than it delivers.')).toBeInTheDocument()
  })

  it('asks for picks with the holding period, profile, and web search chosen', async () => {
    const calls = serve({
      ...STATUS,
      'GET /api/v1/picks': () => json({ items: [] }),
      'POST /api/v1/picks/stream': () =>
        new Response(
          `event: search\ndata: {"query":"fed rates"}\n\n` +
            `event: result\ndata: ${JSON.stringify(picks('Two-week momentum.', { cached: false }))}\n\n`,
        ),
    })
    renderPage()

    await screen.findByText(/No picks yet/)
    await userEvent.click(screen.getByRole('button', { name: '1 week' }))
    await userEvent.clear(screen.getByRole('spinbutton', { name: 'Hold for, weeks' }))
    await userEvent.type(screen.getByRole('spinbutton', { name: 'Hold for, weeks' }), '2')
    await userEvent.type(screen.getByRole('spinbutton', { name: 'Hold for, months' }), '1')
    await userEvent.click(screen.getByRole('button', { name: 'Aggressive' }))
    await userEvent.type(screen.getByRole('spinbutton', { name: /Amount to invest/ }), '2500')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Pick from' }), 'stock')
    await userEvent.type(screen.getByRole('textbox', { name: 'Anything else' }), 'no banks')
    const web = screen.getByRole('checkbox', { name: /Let Claude search the web/ })
    expect(web).toBeChecked() // On until the person turns it off.
    await userEvent.click(web)
    await userEvent.click(screen.getByRole('button', { name: 'Get Claude’s Picks' }))

    expect(await screen.findByText('Two-week momentum.')).toBeInTheDocument()
    expect(calls.find((call) => call.key === 'POST /api/v1/picks/stream')?.body).toEqual({
      profile: {
        horizon: { years: 0, months: 1, weeks: 2 },
        risk_tolerance: 'aggressive',
        amount: 2500,
        weights: 'ideal',
        asset_types: ['stock'],
        notes: 'no banks',
        sell_at_end: true,
        recurring: false,
      },
      web_search: false,
      wait_seconds: 300,
      holdings_portfolios: [],
      added_symbols: [],
      added_first: true,
      extra_context: null,
      refresh: false,
    })
    // The answers are kept for next time.
    expect(JSON.parse(window.localStorage.getItem('pyqxel.picksProfile')!)).toMatchObject({
      months: '1',
      weeks: '2',
      risk: 'aggressive',
      notes: 'no banks',
    })
  })

  it('adds tickers to the candidates, lists every one searchably, and says which are missing', async () => {
    window.localStorage.setItem(
      'pyqxel.picksProfile',
      JSON.stringify({ years: '1', added: ['ionq', 'QTUM', 'NOPE'] }),
    )
    const calls = serve({
      ...STATUS,
      'GET /api/v1/picks': () => json({ items: [] }),
      'POST /api/v1/picks/candidates': () =>
        json({
          candidates: [
            {
              symbol: 'NVDA',
              name: 'NVIDIA Corporation',
              asset_type: 'stock',
              sector: 'Technology',
              lenses: ['largest companies', '12-month momentum'],
              added: false,
            },
            { symbol: 'IONQ', name: 'IonQ, Inc.', asset_type: 'stock', lenses: [], added: true },
            { symbol: 'QTUM', name: 'Quantum ETF', asset_type: 'etf', lenses: [], added: true },
          ],
          missing: ['NOPE'],
          own: true,
        }),
      'POST /api/v1/picks/stream': () =>
        new Response(
          `event: result\ndata: ${JSON.stringify(picks('Quantum.', { cached: false }))}\n\n`,
        ),
    })
    renderPage()

    expect(await screen.findByText(/NOPE is not in your screener/)).toBeInTheDocument()
    await userEvent.click(screen.getByText(/See every ticker Claude would weigh \(3\)/))
    expect(screen.getByText('Leads on largest companies and 12-month momentum')).toBeInTheDocument()
    await userEvent.type(screen.getByRole('searchbox', { name: 'Search the list' }), 'quantum')
    expect(screen.getByRole('link', { name: 'QTUM' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'NVDA' })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Remove NOPE' }))
    await userEvent.click(screen.getByRole('button', { name: 'Get Claude’s Picks' }))
    await screen.findByText('Quantum.')
    const asked = calls.filter((call) => call.key === 'POST /api/v1/picks/candidates')
    expect(asked[0]?.body).toMatchObject({ added_symbols: ['IONQ', 'QTUM', 'NOPE'] })
    expect(calls.find((call) => call.key === 'POST /api/v1/picks/stream')?.body).toMatchObject({
      added_symbols: ['IONQ', 'QTUM'],
    })
  })

  it('will not ask without a holding period of at least a week', async () => {
    serve({ ...STATUS, 'GET /api/v1/picks': () => json({ items: [] }) })
    renderPage()

    await screen.findByText(/No picks yet/)
    await userEvent.clear(screen.getByRole('spinbutton', { name: 'Hold for, years' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Hold for at least one week.')
    expect(screen.getByRole('button', { name: 'Get Claude’s Picks' })).toBeDisabled()
  })

  it('says where to turn on the screener when there is nothing to pick from', async () => {
    serve({
      'GET /api/v1/screener/status': () => json({ stocks: 0 }),
      'GET /api/v1/picks': () => json({ items: [] }),
    })
    renderPage()

    expect(await screen.findByText(/has no securities for your account yet/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'nightly screener refresh' })).toHaveAttribute(
      'href',
      '/screener/refresh',
    )
    expect(screen.getByRole('button', { name: 'Get Claude’s Picks' })).toBeDisabled()
  })

  it('lists saved picks with how they did, opens one with its performance, and deletes it', async () => {
    const older = '11111111-1111-1111-1111-111111111111'
    const saved = (id: string, made: string, horizon: string, gain: number) => ({
      id,
      generated_at: made,
      horizon,
      model: 'claude-opus-5-5',
      headline: 'x',
      symbols: ['NVDA', 'TSLA'],
      ends_on: '2027-11-13',
      ended: horizon === '2 weeks',
      total_return: gain,
      benchmark_return: 0.02,
      excess_return: gain - 0.02,
    })
    const calls = serve({
      ...STATUS,
      'GET /api/v1/picks/latest': () => json(picks('The latest.', { id: 'latest' })),
      'GET /api/v1/picks/performance': (_init, url) =>
        json({
          benchmark: url.searchParams.get('benchmark'),
          benchmark_name:
            url.searchParams.get('benchmark') === 'QQQ' ? 'Nasdaq-100 (QQQ)' : 'S&P 500 (SPY)',
          items: [
            saved('latest', '2026-11-13T12:00:00Z', '1 year', 0.05),
            saved(older, '2026-10-01T12:00:00Z', '2 weeks', -0.04),
          ],
        }),
      [`GET /api/v1/picks/${older}`]: () => json(picks('An earlier idea.', { id: older })),
      [`GET /api/v1/picks/${older}/performance`]: (_init, url) =>
        json({
          id: older,
          benchmark: url.searchParams.get('benchmark'),
          benchmark_name: 'Nasdaq-100 (QQQ)',
          start: '2026-09-30',
          as_of: '2026-10-15',
          ends_on: '2026-10-15',
          ended: true,
          total_return: -0.04,
          benchmark_return: 0.02,
          excess_return: -0.06,
          picks: [
            {
              symbol: 'NVDA',
              weight: 0.6,
              start_price: 100,
              latest_price: 95,
              gain: -0.05,
              contribution: -0.03,
            },
          ],
          series: [],
        }),
      'GET /api/v1/picks/latest/performance': () =>
        json({
          id: 'latest',
          benchmark: 'SPY',
          benchmark_name: 'S&P 500 (SPY)',
          ends_on: '2027-11-13',
          ended: false,
          picks: [],
          series: [],
        }),
      [`DELETE /api/v1/picks/${older}`]: () => new Response(null, { status: 204 }),
    })
    renderPage()

    await screen.findByText('The latest.')
    const savedCard = () => document.getElementById('saved-picks')!
    await screen.findByRole('heading', { name: 'Saved Claude’s Picks' })
    const table = await within(savedCard()).findByRole('table')
    expect(within(table).getByText('+5.0%')).toBeInTheDocument()
    expect(within(table).getByText(/\(ended\)/)).toBeInTheDocument()

    const compare = screen.getByRole('group', { name: 'Compare saved picks with' })
    await userEvent.click(within(compare).getByRole('button', { name: 'Nasdaq-100' }))
    // The list is fetched again against the Nasdaq-100.
    await screen.findByRole('columnheader', { name: /Nasdaq-100/ })
    const compared = within(savedCard()).getByRole('table')
    await userEvent.click(within(compared).getByRole('button', { name: /Oct/ }))

    expect(await screen.findByText('An earlier idea.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Earlier Picks' })).toBeInTheDocument()
    expect(await screen.findByText(/tracking stopped then/)).toBeInTheDocument()
    const shown = document.getElementById('picks-performance')!
    expect(within(shown).getAllByText('-6.0%').length).toBeGreaterThan(0)
    expect(calls.some((call) => call.key === `GET /api/v1/picks/${older}/performance`)).toBe(true)
    expect(screen.getByRole('link', { name: 'Show in the screener' })).toHaveAttribute(
      'href',
      `/screener?picks=${older}`,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Delete these picks' }))

    expect(await screen.findByText('The latest.')).toBeInTheDocument()
    expect(calls.some((call) => call.key === `DELETE /api/v1/picks/${older}`)).toBe(true)
  })
})
