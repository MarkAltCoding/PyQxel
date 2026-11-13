import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BillingStatus } from '../api/types'
import { periodBills, type PeriodBill } from '../lib/billing'
import { BillingCard } from './BillingCard'

const STATUS: BillingStatus = {
  enabled: true,
  exempt: false,
  credit_usd: 4.5,
  subscription: null,
  can_refresh: true,
  last_30_days_usd: 0.9,
  last_30_days_runs: 30,
  entries: [
    {
      kind: 'usage',
      credit_usd: -0.012,
      subscription_usd: 0,
      created_at: '2026-11-09T06:00:00Z',
    },
    { kind: 'top_up', credit_usd: 5, subscription_usd: 0, created_at: '2026-11-01T12:00:00Z' },
  ],
  min_top_up_cents: 500,
  max_top_up_cents: 50000,
  card_fee_cents: 30,
  card_fee_rate: 0.029,
  billing_fee_rate: 0.007,
  international_fee_rate: 0.015,
  dispute_fee_cents: 1500,
  min_charge_cents: 50,
}

afterEach(() => vi.restoreAllMocks())

function renderCard(route = '/settings') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <BillingCard />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function answer(status: Partial<BillingStatus>, link = 'https://checkout.stripe.com/c/pay/cs') {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async (_url, init) =>
      init?.method === 'POST'
        ? new Response(JSON.stringify({ url: link }))
        : new Response(JSON.stringify({ ...STATUS, ...status })),
    )
}

describe('BillingCard', () => {
  it('shows nothing on a server that does not bill', async () => {
    const fetch = answer({ enabled: false })
    const { container } = renderCard()

    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the credit, recent cost, and history', async () => {
    answer({})
    renderCard()

    expect(await screen.findByText('$4.50')).toBeInTheDocument()
    expect(screen.getByText(/about 150 days at your recent pace/i)).toBeInTheDocument()
    expect(screen.getByText('Nightly refresh')).toBeInTheDocument()
    expect(screen.getByText('-$0.012 credit')).toBeInTheDocument()
  })

  it('sends the chosen amount to Stripe and opens its page', async () => {
    const fetch = answer({})
    const assign = vi.fn()
    vi.spyOn(window, 'location', 'get').mockReturnValue({
      ...window.location,
      assign,
    } as Location)
    renderCard()

    await userEvent.click(await screen.findByRole('button', { name: '$20' }))
    await userEvent.click(
      screen.getByRole('button', { name: /pay \$20\.91 for \$20\.00 of credit/i }),
    )

    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://checkout.stripe.com/c/pay/cs'))
    const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(String(post?.[0])).toMatch(/\/me\/billing\/top-up$/)
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ amount_cents: 2000 })
  })

  it('says a refresh is paused with no credit or subscription', async () => {
    answer({ credit_usd: 0, can_refresh: false })
    renderCard()

    expect(await screen.findByText(/paused until you add credit/i)).toBeInTheDocument()
  })

  it('thanks the user back from Stripe', async () => {
    answer({})
    renderCard('/settings?billing=paid')

    expect(await screen.findByText(/your credit appears here/i)).toBeInTheDocument()
  })

  it('explains a payment from the EU turned away, and offers no other', async () => {
    answer({ closed_country: 'DE' })
    renderCard('/settings?billing=paid')

    expect(
      await screen.findByText(/your payment from germany was turned away/i),
    ).toBeInTheDocument()
    expect(screen.queryByText(/your credit appears here/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /of credit/i })).not.toBeInTheDocument()
  })
})

describe('periodBills', () => {
  it('shows a weekly bill losing far more to fees than a yearly one', () => {
    const [week, month, year] = periodBills(STATUS) as [PeriodBill, PeriodBill, PeriodBill]

    // $0.90 a month: 21 cents a week, charged at Stripe's 50-cent minimum.
    expect(week.chargeCents).toBe(50)
    expect(week.feeShare).toBeCloseTo((30 + 50 * 0.036) / 50, 5)
    expect(month.feeShare).toBeCloseTo((30 + 90 * 0.036) / 90, 5)
    // $10.95 a year: 30 cents plus 3.6% (the card's 2.9% and Billing's 0.7%).
    expect(year.feeShare).toBeCloseTo((30 + 1095 * 0.036) / 1095, 5)
  })

  it('has nothing to show before refreshes have cost anything', () => {
    expect(periodBills({ ...STATUS, last_30_days_usd: 0 })).toEqual([])
  })
})
