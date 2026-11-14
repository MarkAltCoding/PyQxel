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
const STATUS = { 'GET /api/v1/screener/status': () => json({ stocks: 5200, own: true }) }

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
    await userEvent.click(screen.getByRole('checkbox', { name: /Let Claude search the web/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Get Claude’s picks' }))

    expect(await screen.findByText('Two-week momentum.')).toBeInTheDocument()
    expect(calls.find((call) => call.key === 'POST /api/v1/picks/stream')?.body).toEqual({
      profile: {
        horizon: { years: 0, months: 1, weeks: 2 },
        risk_tolerance: 'aggressive',
        amount: 2500,
        asset_types: ['stock'],
        notes: 'no banks',
      },
      web_search: true,
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

  it('will not ask without a holding period of at least a week', async () => {
    serve({ ...STATUS, 'GET /api/v1/picks': () => json({ items: [] }) })
    renderPage()

    await screen.findByText(/No picks yet/)
    await userEvent.clear(screen.getByRole('spinbutton', { name: 'Hold for, years' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Hold for at least one week.')
    expect(screen.getByRole('button', { name: 'Get Claude’s picks' })).toBeDisabled()
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
      '/settings#nightly-refresh',
    )
    expect(screen.getByRole('button', { name: 'Get Claude’s picks' })).toBeDisabled()
  })

  it('opens earlier picks from the history, and deletes them', async () => {
    const older = '11111111-1111-1111-1111-111111111111'
    const calls = serve({
      ...STATUS,
      'GET /api/v1/picks/latest': () => json(picks('The latest.')),
      'GET /api/v1/picks': () =>
        json({
          items: [
            { id: 'latest', generated_at: '2026-11-13T12:00:00Z', horizon: '1 year', symbols: [] },
            { id: older, generated_at: '2026-10-01T12:00:00Z', horizon: '2 weeks', symbols: [] },
          ],
        }),
      [`GET /api/v1/picks/${older}`]: () => json(picks('An earlier idea.', { id: older })),
      [`DELETE /api/v1/picks/${older}`]: () => new Response(null, { status: 204 }),
    })
    renderPage()

    await screen.findByText('The latest.')
    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Show' }), older)

    expect(await screen.findByText('An earlier idea.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Earlier picks' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Show in the screener' })).toHaveAttribute(
      'href',
      `/screener?picks=${older}`,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Delete these picks' }))

    expect(await screen.findByText('The latest.')).toBeInTheDocument()
    expect(calls.some((call) => call.key === `DELETE /api/v1/picks/${older}`)).toBe(true)
  })
})
