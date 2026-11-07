import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearProviderKeys, setProviderKey } from '../lib/providerKeys'
import { PortfolioReviewCard } from './PortfolioReviewCard'

const review = (headline: string) => ({
  id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
  period: '1y',
  report: {
    headline,
    risk_level: 'elevated',
    diversification: 'fair',
    summary: 'Two of three holdings are technology stocks.',
    strengths: ['XOM diversifies the rest.'],
    key_risks: [{ name: 'Sector concentration', severity: 'high', evidence: '67% technology.' }],
    plan_summary: 'Trim technology into an international fund.',
    target_allocation: [
      { symbol: 'AAPL', target_weight: 0.4, order_type: 'market', rationale: 'Trim.' },
      { symbol: 'VXUS', target_weight: 0.6, order_type: 'limit', rationale: 'Add.' },
    ],
    considerations: ['Selling realizes gains.'],
    data_limitations: ['No valuations.'],
  },
  plan: {
    total_value: 10000,
    trades: [
      {
        symbol: 'AAPL',
        action: 'sell',
        shares: 30,
        price: 200,
        value: 6000,
        order_type: 'market',
        rationale: 'Trim.',
      },
      {
        symbol: 'VXUS',
        action: 'buy',
        shares: 119,
        price: 50,
        value: 5950,
        order_type: 'limit',
        limit_price: 49.75,
        rationale: 'Add.',
      },
    ],
    cash_after: 50,
    notes: [],
  },
  context: {
    holdings: [{ symbol: 'AAPL', weight: 1 }],
    portfolio: { volatility: 0.2, max_drawdown: -0.25 },
  },
  model: 'claude-opus-5-5',
  generated_at: '2026-10-29T12:00:00Z',
  cached: true,
  disclaimer: 'Not investment advice.',
})

function renderCard(props: { hasHoldings: boolean; stale: boolean; canCombine?: boolean }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PortfolioReviewCard portfolio={2} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

// Claude writes on the account's own Anthropic key, which these tests have.
beforeEach(() => setProviderKey('anthropic', 'sk-ant-test'))
afterEach(() => {
  vi.restoreAllMocks()
  clearProviderKeys()
})

describe('PortfolioReviewCard', () => {
  it('without an Anthropic key, says so and points to Claude outside the app', async () => {
    clearProviderKeys()
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail: 'No portfolio review yet.' }), { status: 404 }),
    )
    renderCard({ hasHoldings: true, stale: false })

    expect(await screen.findByText(/needs an Anthropic key/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Settings' })).toHaveAttribute(
      'href',
      '/settings#provider-keys',
    )
    expect(screen.getByRole('button', { name: 'Review my portfolio' })).toBeDisabled()
    expect(screen.getByText('Use Claude outside the app, without API costs')).toBeInTheDocument()
  })

  it('shows the stored review, then streams a new one', async () => {
    const posted: unknown[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)))
        const result = { ...review('Rebalanced view.'), cached: false }
        return new Response(`event: result\ndata: ${JSON.stringify(result)}\n\n`)
      }
      expect(String(input)).toContain('/api/v1/portfolio/analysis?portfolio=2')
      return new Response(JSON.stringify(review('Concentrated in technology.')))
    })
    renderCard({ hasHoldings: true, stale: false })

    expect(await screen.findByText('Concentrated in technology.')).toBeInTheDocument()
    expect(screen.getByText(/reused, not billed again/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('tab', { name: /Plan/ }))
    expect(screen.getByText('Limit at $49.75')).toBeInTheDocument()
    expect(screen.getByText('119')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Write a new review' }))

    expect(await screen.findByText('Rebalanced view.')).toBeInTheDocument()
    expect(posted).toEqual([
      {
        portfolio: 2,
        combined: false,
        period: '1y',
        refresh: true,
        profile: { risk_tolerance: null, horizon_years: null, cash_to_invest: 0, notes: null },
      },
    ])
  })

  it('reviews both portfolios together when chosen, and keeps that review apart', async () => {
    const posted: Record<string, unknown>[] = []
    const read: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)))
        const result = {
          ...review('One household portfolio.'),
          cached: false,
          context: {
            ...review('').context,
            accounts: [
              { account: 'Brokerage', value: 6000, holdings: 3 },
              { account: 'Roth IRA', value: 4000, holdings: 2 },
            ],
          },
        }
        return new Response(`event: result\ndata: ${JSON.stringify(result)}\n\n`)
      }
      read.push(String(input))
      return new Response(JSON.stringify({ detail: 'No portfolio review yet.' }), { status: 404 })
    })
    renderCard({ hasHoldings: true, stale: false, canCombine: true })

    await userEvent.click(await screen.findByRole('button', { name: 'Both together' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Review both together' }))

    expect(await screen.findByText('One household portfolio.')).toBeInTheDocument()
    expect(
      screen.getByText(/Covers Brokerage \(\$6,000\) and Roth IRA \(\$4,000\) together/),
    ).toBeInTheDocument()
    expect(posted[0]).toMatchObject({ combined: true })
    expect(read.at(-1)).toContain('combined=true')
  })

  it('shows cash as Cash or its fund, never the ticker CASH, and holds as holds', async () => {
    const stored = review('Cash and holds.')
    const withCash = {
      ...stored,
      report: {
        ...stored.report,
        target_allocation: [
          {
            symbol: 'AAPL',
            target_weight: 1,
            order_type: 'market',
            rationale: 'Keep.',
            keep: true,
          },
          // A review written before cash was marked.
          { symbol: 'CASH', target_weight: 0, order_type: 'market', rationale: 'No cash.' },
        ],
      },
      plan: { ...stored.plan, cash_symbol: 'SPAXX', cash_after: 50 },
    }
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(withCash)))
    renderCard({ hasHoldings: true, stale: false })

    await screen.findByText('Cash and holds.')
    await userEvent.click(screen.getByRole('tab', { name: /Plan/ }))
    const allocation = screen
      .getByRole('heading', { name: 'Allocation: now and target' })
      .closest('.allocation') as HTMLElement
    expect(within(allocation).getByText('Cash')).toBeInTheDocument()
    expect(within(allocation).queryByText('CASH')).toBeNull()
    expect(within(allocation).getAllByText('Hold')).toHaveLength(2)
    expect(screen.getByText(/Cash after \(in SPAXX\)/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Full text' }))
    const table = screen.getByRole('columnheader', { name: 'Position' }).closest('table')!
    expect(within(table).getByText('Cash')).toBeInTheDocument()
    expect(within(table).queryByText('CASH')).toBeNull()
    expect(within(table).getByText(/hold/)).toBeInTheDocument()
    expect(screen.getByText(/Cash after \(in SPAXX\)/)).toBeInTheDocument()
  })

  it('offers no combined review until both portfolios have holdings', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'No portfolio review yet.' }), { status: 404 }),
    )
    renderCard({ hasHoldings: true, stale: false })
    expect(await screen.findByText('No review yet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Both together' })).toBeNull()
  })

  it('saves a review pasted from Claude outside the app with its exported data', async () => {
    const bodies: Record<string, unknown>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const path = new URL(String(input), 'http://localhost').pathname
      if (path.endsWith('/prompt')) {
        return new Response(
          JSON.stringify({ prompt: '## Request', filename: 'p.md', context: { period: '1y' } }),
        )
      }
      if (path.endsWith('/import')) {
        bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>)
        return new Response(JSON.stringify({ ...review('Pasted review.'), cached: false }))
      }
      return new Response('{}', { status: 404 })
    })
    renderCard({ hasHoldings: true, stale: false })

    await userEvent.click(await screen.findByText(/Use Claude outside the app/))
    await userEvent.click(screen.getByRole('button', { name: 'Get the prompt' }))
    await screen.findByRole('button', { name: 'Copy prompt' })
    await userEvent.type(screen.getByLabelText('Claude’s answer'), 'the answer')
    await userEvent.click(screen.getByRole('button', { name: 'Save answer' }))

    expect(await screen.findByText('Pasted review.')).toBeInTheDocument()
    expect(bodies[0]).toMatchObject({
      context: { period: '1y' },
      request: { portfolio: 2, period: '1y' },
      response: 'the answer',
    })
  })

  it('shows the whole review as text when asked, and remembers the choice', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      async () => new Response(JSON.stringify(review('Concentrated in technology.'))),
    )
    renderCard({ hasHoldings: true, stale: false })
    await screen.findByText('Concentrated in technology.')
    expect(screen.getByRole('tablist', { name: 'Parts of the review' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Full text' }))

    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recommended plan' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: 'Add.' })).toBeInTheDocument()
    expect(screen.getByText('Limit at $49.75')).toBeInTheDocument()
    expect(window.localStorage.getItem('pyqxel.reportView')).toBe('text')
  })

  it('asks for a custom length of price history, and shows fractional orders plainly', async () => {
    const posted: Record<string, unknown>[] = []
    const fractional = {
      ...review('Trim AEHR.'),
      plan: {
        total_value: 10000,
        cash_after: 9.8,
        notes: ['Fractional orders (AEHR): not every broker trades fractions.'],
        trades: [
          {
            symbol: 'AEHR',
            action: 'sell',
            shares: 0.49,
            price: 20,
            value: 9.8,
            order_type: 'limit',
            limit_price: 19.9,
            rationale: 'Exit.',
            fractional: true,
          },
        ],
      },
    }
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)) as Record<string, unknown>)
        return new Response(`event: result\ndata: ${JSON.stringify(fractional)}\n\n`)
      }
      return new Response('{}', { status: 404 })
    })
    renderCard({ hasHoldings: true, stale: false })

    await userEvent.click(await screen.findByRole('button', { name: 'Custom' }))
    const months = screen.getByRole('spinbutton', { name: /Price history to measure, custom/ })
    await userEvent.clear(months)
    await userEvent.type(months, '18')
    await userEvent.tab()
    await userEvent.click(screen.getByRole('button', { name: 'Review my portfolio' }))

    expect(await screen.findByText('Trim AEHR.')).toBeInTheDocument()
    expect(posted[0]).toMatchObject({ portfolio: 2, history_months: 18 })
    expect(posted[0]).not.toHaveProperty('period')
    await userEvent.click(screen.getByRole('tab', { name: /Plan/ }))
    expect(screen.getByRole('cell', { name: '0.49' })).toBeInTheDocument()
    expect(screen.getByRole('cell', { name: /Sell\s*Fractional/ })).toBeInTheDocument()
    expect(screen.getByText('Limit at $19.90, day order')).toBeInTheDocument()
    expect(screen.getByText(/Fractional orders \(AEHR\)/)).toBeInTheDocument()
  })

  it('waits for saved holdings before reviewing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 404 }))
    renderCard({ hasHoldings: true, stale: true })

    expect(await screen.findByText(/Save your changes/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review my portfolio' })).toBeDisabled()
  })
})
