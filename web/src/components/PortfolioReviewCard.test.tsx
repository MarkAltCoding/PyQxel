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

const PORTFOLIOS = [
  { portfolio: 1, name: 'Brokerage', holdings: 3, account_type: 'taxable' },
  { portfolio: 2, name: 'Roth IRA', holdings: 2, account_type: 'roth_ira', contribution_room: 100 },
  { portfolio: 3, name: 'HSA', holdings: 1 },
  { portfolio: 4, name: 'Portfolio 4', holdings: 0 },
] as const

function renderCard(props: {
  hasHoldings: boolean
  stale: boolean
  portfolios?: readonly (typeof PORTFOLIOS)[number][]
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <PortfolioReviewCard
          portfolio={2}
          hasHoldings={props.hasHoldings}
          stale={props.stale}
          portfolios={props.portfolios?.map((p) => ({ ...p }))}
        />
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
        period: '1y',
        web_search: true,
        refresh: true,
        profile: {
          risk_tolerance: null,
          horizon_years: null,
          cash_to_invest: 0,
          notes: null,
          fractional_shares: true,
          round_shares: true,
        },
      },
    ])
  })

  it('sends a horizon in months as a fraction of a year, and new cash in dollars', async () => {
    const posted: { profile: Record<string, unknown> }[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)))
        const result = { ...review('Short horizon.'), cached: false }
        return new Response(`event: result\ndata: ${JSON.stringify(result)}\n\n`)
      }
      return new Response(JSON.stringify(review('Concentrated in technology.')))
    })
    renderCard({ hasHoldings: true, stale: false })
    await screen.findByText('Concentrated in technology.')

    await userEvent.type(screen.getByLabelText('Horizon'), '6')
    await userEvent.selectOptions(screen.getByLabelText('Horizon unit'), 'months')
    await userEvent.type(screen.getByLabelText('New cash to invest, in dollars'), '1500')
    await userEvent.click(screen.getByRole('button', { name: 'Write a new review' }))

    await screen.findByText('Short horizon.')
    expect(posted[0]?.profile).toMatchObject({ horizon_years: 0.5, cash_to_invest: 1500 })
  })

  it('reviews the portfolios chosen as one, keeping that review apart', async () => {
    const posted: Record<string, unknown>[] = []
    const read: string[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)))
        const result = {
          ...review('One household portfolio.'),
          cached: false,
          group_mode: 'combined',
          portfolios: [1, 2],
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
    renderCard({ hasHoldings: true, stale: false, portfolios: PORTFOLIOS })

    await userEvent.click(await screen.findByRole('button', { name: 'Several together' }))
    // Every portfolio with holdings starts chosen; the empty one is not offered.
    expect(screen.queryByRole('checkbox', { name: /Portfolio 4/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('checkbox', { name: /HSA/ }))
    await userEvent.click(await screen.findByRole('button', { name: 'Review them together' }))

    expect(await screen.findByText('One household portfolio.')).toBeInTheDocument()
    expect(
      screen.getByText(/Covers Brokerage \(\$6,000\) and Roth IRA \(\$4,000\) together, as one/),
    ).toBeInTheDocument()
    expect(posted[0]).toMatchObject({ portfolios: [1, 2], group_mode: 'combined', accounts: [] })
    expect(posted[0]).not.toHaveProperty('portfolio')
    expect(read.at(-1)).toContain('portfolios=1&portfolios=2&mode=combined')
  })

  it('gives each account its own goals and cash, with new cash entered once', async () => {
    const posted: Record<string, unknown>[] = []
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (init?.method === 'POST') {
        posted.push(JSON.parse(String(init.body)))
        const base = review('Two plans.')
        const result = {
          ...base,
          cached: false,
          group_mode: 'separate',
          portfolios: [1, 2],
          plan: null,
          report: {
            ...base.report,
            target_allocation: [],
            account_plans: [
              {
                account: 'Roth IRA',
                headline: 'Grow it here.',
                plan_summary: 'Buy the fund.',
                new_cash: 100,
                target_allocation: [
                  { symbol: 'VXUS', target_weight: 1, order_type: 'market', rationale: 'Add.' },
                ],
              },
            ],
          },
          account_orders: [{ account: 'Roth IRA', portfolio: 2, plan: base.plan }],
          context: {
            ...base.context,
            accounts: [
              { account: 'Roth IRA', value: 4000, holdings: 2, account_type_name: 'Roth IRA' },
            ],
          },
        }
        return new Response(`event: result\ndata: ${JSON.stringify(result)}\n\n`)
      }
      if (String(input).includes('contribution-limits')) {
        return new Response(
          JSON.stringify({
            as_of: '2026-11-19',
            outdated: false,
            current: {
              year: 2026,
              limits: [{ account: 'Traditional and Roth IRAs', limit: 7500, catch_up_50: 1100 }],
              roth_ira_phase_out_single: [153000, 168000],
              roth_ira_phase_out_joint: [242000, 252000],
              source: 'IRS',
              source_url: 'https://www.irs.gov/',
            },
            next: null,
            notes: ['The limits rise with inflation.'],
          }),
        )
      }
      return new Response(JSON.stringify({ detail: 'No portfolio review yet.' }), { status: 404 })
    })
    renderCard({ hasHoldings: true, stale: false, portfolios: PORTFOLIOS })

    await userEvent.click(await screen.findByRole('button', { name: 'Several together' }))
    await userEvent.click(screen.getByRole('checkbox', { name: /HSA/ }))
    await userEvent.click(screen.getByRole('button', { name: 'Each with its own plan' }))
    await userEvent.click(screen.getByRole('button', { name: 'Goals for each account' }))
    await userEvent.click(screen.getByText('Tailor the plan to your goals (optional)'))

    expect(screen.queryByRole('group', { name: 'Risk tolerance' })).not.toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('New cash to invest, in dollars'), '500')
    await userEvent.type(screen.getByLabelText('Cash in Brokerage to trade, in dollars'), '250')
    await userEvent.click(
      within(screen.getByRole('group', { name: 'Risk tolerance for Roth IRA' })).getByRole(
        'button',
        { name: 'Aggressive' },
      ),
    )
    await userEvent.type(screen.getByLabelText('Horizon for Roth IRA'), '30')
    expect(screen.getByText(/\$100 room left/)).toBeInTheDocument()
    expect(await screen.findByText(/Yearly contribution limits \(2026\)/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Review them together' }))

    await userEvent.click(await screen.findByRole('tab', { name: /Plans/ }))
    expect(await screen.findByText('Grow it here.')).toBeInTheDocument()
    expect(screen.getByText('Deposit $100 of the new cash here')).toBeInTheDocument()
    expect(posted[0]).toMatchObject({
      portfolios: [1, 2],
      group_mode: 'separate',
      profile: { cash_to_invest: 500 },
      accounts: [
        { portfolio: 1, cash_available: 250, risk_tolerance: null },
        { portfolio: 2, cash_available: 0, risk_tolerance: 'aggressive', horizon_years: 30 },
      ],
    })
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
    expect(screen.queryByRole('button', { name: 'All together' })).toBeNull()
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
