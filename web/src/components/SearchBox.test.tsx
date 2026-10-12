import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SearchBox } from './SearchBox'

function renderSearch(onSelect: (symbol: string) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <SearchBox onSelect={onSelect} />
    </QueryClientProvider>,
  )
}

const results = {
  query: 'app',
  items: [
    {
      symbol: 'AAPL',
      name: 'Apple Inc.',
      exchange: 'NMS',
      sector: 'Technology',
      market_cap: 4.9e12,
    },
    { symbol: 'APP', name: 'AppLovin', exchange: 'NMS', sector: 'Technology', market_cap: 1e11 },
  ],
}

afterEach(() => vi.restoreAllMocks())

describe('SearchBox', () => {
  it('lists matches and picks one with the keyboard', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(results)))
    const onSelect = vi.fn()
    renderSearch(onSelect)

    await userEvent.type(screen.getByRole('combobox'), 'app')
    expect(await screen.findByText('Apple Inc.')).toBeInTheDocument()
    await userEvent.keyboard('{ArrowDown}{Enter}')

    expect(onSelect).toHaveBeenCalledWith('APP')
  })

  it('offers any ticker outside the universe, such as an ETF', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ query: 'tlt', items: [] })),
    )
    const onSelect = vi.fn()
    renderSearch(onSelect)

    await userEvent.type(screen.getByRole('combobox'), 'tlt')
    await userEvent.click(await screen.findByText('Go to TLT'))

    expect(onSelect).toHaveBeenCalledWith('TLT')
  })
})
