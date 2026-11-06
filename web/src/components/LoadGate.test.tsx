import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { UsageEstimate } from '../api/types'
import { forgetAgreements, loadsWithoutAsking } from '../lib/usage'
import { LoadGate } from './LoadGate'

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })

const SPENDS: UsageEstimate = {
  from_store: 2,
  providers: [
    {
      provider: 'tiingo',
      name: 'Tiingo',
      requests: 12,
      new_symbols: 3,
      tightest: {
        window: 'hour',
        unit: 'requests',
        limit: 50,
        used: 42,
        remaining: 8,
        resets: 'Every hour',
        resets_at: '2026-11-05T21:00:00Z',
        source: 'pyqxel',
      },
    },
  ],
}

afterEach(() => {
  vi.restoreAllMocks()
  forgetAgreements()
})

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <LoadGate
        page="portfolio"
        signature="1:MU,GOOG"
        what="this portfolio’s prices"
        fetches={[{ kind: 'history', symbols: ['MU', 'GOOG'] }]}
      >
        <p>The prices</p>
      </LoadGate>
    </QueryClientProvider>,
  )
}

describe('LoadGate', () => {
  it('says what loading would spend on each key, and loads once agreed', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(SPENDS))
    renderGate()

    expect(
      await screen.findByRole('heading', { name: /Load this portfolio’s prices/ }),
    ).toBeInTheDocument()
    expect(screen.getByText('12 requests')).toBeInTheDocument()
    expect(screen.getByText(/with 8 of its 50 requests an hour left/)).toBeInTheDocument()
    expect(
      screen.getByText(/not enough; the rest would fall to your other keys/),
    ).toBeInTheDocument()
    expect(screen.getByText(/3 of the symbols count as new/)).toBeInTheDocument()
    expect(
      screen.getByText(/2 price histories come from prices PyQxel already keeps/),
    ).toBeInTheDocument()
    expect(screen.queryByText('The prices')).not.toBeInTheDocument()
    const body = JSON.parse(String(fetch.mock.calls[0]![1]!.body))
    expect(body.fetches).toEqual([{ kind: 'history', symbols: ['MU', 'GOOG'] }])

    await userEvent.click(screen.getByRole('button', { name: 'Load' }))

    expect(screen.getByText('The prices')).toBeInTheDocument()
    expect(loadsWithoutAsking('portfolio')).toBe(false)
  })

  it('remembers a choice to load the page without asking', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json(SPENDS))
    renderGate()

    await userEvent.click(
      await screen.findByRole('button', { name: /don’t ask again for Portfolio/ }),
    )

    expect(screen.getByText('The prices')).toBeInTheDocument()
    expect(loadsWithoutAsking('portfolio')).toBe(true)
  })

  it('loads at once when nothing would be spent', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      json({ from_store: 2, providers: [] }),
    )
    renderGate()

    expect(await screen.findByText('The prices')).toBeInTheDocument()
  })
})
