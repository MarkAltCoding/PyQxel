import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
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

function renderCard(props: { hasHoldings: boolean; stale: boolean }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <PortfolioReviewCard portfolio={2} {...props} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('PortfolioReviewCard', () => {
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
    expect(screen.getByText('Limit at $49.75')).toBeInTheDocument()
    expect(screen.getByText('119')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Write a new review' }))

    expect(await screen.findByText('Rebalanced view.')).toBeInTheDocument()
    expect(posted).toEqual([
      {
        portfolio: 2,
        period: '1y',
        refresh: true,
        profile: { risk_tolerance: null, horizon_years: null, cash_to_invest: 0, notes: null },
      },
    ])
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

  it('waits for saved holdings before reviewing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 404 }))
    renderCard({ hasHoldings: true, stale: true })

    expect(await screen.findByText(/Save your changes/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Review my portfolio' })).toBeDisabled()
  })
})
