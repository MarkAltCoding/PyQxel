import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { MarketCalendar, NewsArticle } from '../api/types'
import { MarketsPage } from './MarketsPage'

const CALENDAR: MarketCalendar = {
  start: '2026-10-25',
  end: '2026-11-23',
  fomc: [{ start: '2026-10-27', end: '2026-10-28', projections: false }],
  earnings: [{ symbol: 'AAPL', name: 'Apple Inc.', date: '2026-10-29', market_cap: 3e12 }],
  ipos: [{ date: '2026-10-26', name: 'Big Co', symbol: 'BIG', status: 'expected' }],
  holidays: [{ date: '2026-11-26', name: 'Thanksgiving Day', trading_hours: null }],
  status: null,
  notice: null,
}

/** `count` articles of `category`, the later ones more important and older. */
const articles = (category: string, count: number): NewsArticle[] =>
  Array.from({ length: count }, (_, i) => ({
    headline: `${category} headline ${i + 1}`,
    source: 'Reuters',
    url: `https://example.com/${category}/${i + 1}`,
    published_at: new Date(Date.UTC(2026, 9, 25, 12) - i * 3_600_000).toISOString(),
    importance: i / 100,
  }))

const NEWS: Record<string, NewsArticle[]> = {
  general: articles('general', 25),
  crypto: articles('crypto', 3),
}

let calls: URL[]

beforeEach(() => {
  calls = []
  window.localStorage.clear()
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push(url)
    const body =
      url.pathname === '/api/v1/markets/calendar'
        ? CALENDAR
        : {
            category: url.searchParams.get('category'),
            articles: NEWS[url.searchParams.get('category') ?? ''] ?? [],
            fetched_at: '2026-10-25T12:00:00Z',
          }
    return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  })
})

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <MarketsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const calendarCalls = () => calls.filter((url) => url.pathname === '/api/v1/markets/calendar')
const headlines = () =>
  within(screen.getByRole('heading', { name: 'Market news' }).closest('section')!)
    .getAllByRole('link')
    .map((link) => link.textContent)

describe('the Markets page', () => {
  it('shows any combination of event kinds', async () => {
    renderPage()
    const events = await screen.findByRole('group', { name: 'Events' })
    expect(await screen.findByText(/Big Co/)).toBeInTheDocument()

    await userEvent.click(within(events).getByRole('button', { name: 'IPOs' }))
    await userEvent.click(within(events).getByRole('button', { name: 'Holidays' }))

    expect(screen.queryByText(/Big Co/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Thanksgiving/)).not.toBeInTheDocument()
    expect(screen.getByText('FOMC rate decision')).toBeInTheDocument()
    expect(screen.getByText('Apple Inc. (AAPL)')).toBeInTheDocument()

    // Without earnings, the market cap filter has nothing to filter.
    await userEvent.click(within(events).getByRole('button', { name: 'Earnings' }))
    expect(
      screen.queryByRole('group', { name: 'Companies reporting, by market cap' }),
    ).not.toBeInTheDocument()
  })

  it('asks for the market cap and window chosen, preset or custom', async () => {
    renderPage()
    const caps = await screen.findByRole('group', { name: 'Companies reporting, by market cap' })

    await userEvent.click(within(caps).getByRole('button', { name: '$500B+' }))
    await waitFor(() =>
      expect(calendarCalls().at(-1)?.searchParams.get('min_market_cap')).toBe('500000000000'),
    )

    await userEvent.click(
      within(screen.getByRole('group', { name: 'Window' })).getByRole('button', { name: 'Custom' }),
    )
    const days = screen.getByRole('spinbutton', { name: 'Window, custom (days)' })
    await userEvent.clear(days)
    await userEvent.type(days, '45')
    await waitFor(() => expect(calendarCalls().at(-1)?.searchParams.get('days')).toBe('45'))
    expect(calendarCalls().some((url) => url.searchParams.get('days') === '4')).toBe(false)
  })

  it('pages the most important headlines ten at a time, across the categories chosen', async () => {
    renderPage()
    await waitFor(() => expect(headlines()).toHaveLength(10))
    expect(headlines()[0]).toBe('general headline 25')
    expect(screen.getByText('1–10 of 25 headlines')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Page 3' }))
    expect(headlines()).toHaveLength(5)

    await userEvent.click(
      within(screen.getByRole('group', { name: 'News categories' })).getByRole('button', {
        name: 'Crypto',
      }),
    )
    await waitFor(() => expect(screen.getByText('1–10 of 28 headlines')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Newest' }))
    expect(headlines().slice(0, 2)).toEqual(['general headline 1', 'crypto headline 1'])

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Per page' }), '50')
    expect(headlines()).toHaveLength(28)
    expect(window.localStorage.getItem('pyqxel.marketNewsPerPage')).toBe('50')
  })
})
