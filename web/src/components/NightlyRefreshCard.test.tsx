import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
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

function renderCard(withFinnhub = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <NightlyRefreshCard account={account(withFinnhub)} />
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
    expect(JSON.parse(String(put![1]!.body))).toMatchObject({ refresh_time: '19:00' })
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
    await screen.findByText(/Add your Finnhub key above first/)
    expect(box).toBeDisabled()
  })
})
