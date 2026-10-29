import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { NewsResponse } from '../api/types'
import { NewsCard } from './NewsCard'

vi.mock('../charts/EChart', () => ({
  EChart: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
}))

const NEWS: NewsResponse = {
  symbol: 'AAPL',
  start: '2026-10-15',
  end: '2026-10-29',
  overview: {
    total_articles: 25,
    last_day_articles: 3,
    last_week_articles: 12,
    daily_average: 1.8,
    busiest_day: { day: '2026-10-28', articles: 6 },
    sources: [{ source: 'Reuters', articles: 25 }],
    daily: [],
  },
  articles: Array.from({ length: 25 }, (_, i) => ({
    headline: `Apple headline ${i + 1}`,
    source: 'Reuters',
    url: `https://example.com/aapl/${i + 1}`,
    published_at: new Date(Date.UTC(2026, 9, 29, 12) - i * 3_600_000).toISOString(),
  })),
  fetched_at: '2026-10-29T12:00:00Z',
}

const headlines = () =>
  within(document.querySelector<HTMLElement>('.news-list')!)
    .getAllByRole('link')
    .map((link) => link.textContent)

beforeEach(() => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(JSON.stringify(NEWS)))
})

afterEach(() => vi.restoreAllMocks())

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NewsCard symbol="AAPL" />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('NewsCard', () => {
  it('pages the headlines ten at a time, as the market news does', async () => {
    renderCard()

    await waitFor(() => expect(headlines()).toHaveLength(10))
    expect(headlines()[0]).toBe('Apple headline 1')
    expect(screen.getByText('1–10 of 25 headlines')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Page 3' }))
    expect(headlines()).toEqual(Array.from({ length: 5 }, (_, i) => `Apple headline ${i + 21}`))

    await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Per page' }), '20')
    expect(headlines()).toHaveLength(20)
    expect(screen.getByText('1–20 of 25 headlines')).toBeInTheDocument()
    expect(window.localStorage.getItem('pyqxel.marketNewsPerPage')).toBe('20')
  })
})
