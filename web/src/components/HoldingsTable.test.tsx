import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HoldingValue } from '../api/types'
import { HoldingsTable, type HoldingRow } from './HoldingsTable'

const holdings: HoldingValue[] = [
  { symbol: 'MSFT', shares: 3, name: 'Microsoft', price: 500, value: 1500, weight: 0.3 },
  { symbol: 'AAPL', shares: 10, name: 'Apple', price: 200, value: 2000, weight: 0.4 },
  {
    symbol: 'SPAXX',
    shares: 500,
    name: 'Fidelity Govt Money Market',
    price: 1,
    value: 500,
    weight: 0.1,
    cash: true,
  },
  { symbol: 'NEWCO', shares: 50, name: 'Newco', price: 20, value: 1000, weight: 0.2 },
]
const rows: HoldingRow[] = holdings.map((h) => ({ symbol: h.symbol, shares: String(h.shares) }))

const changesFor = (window: string) => ({
  window,
  start: '2025-11-01',
  changes: [
    { symbol: 'MSFT', start: '2025-10-31', change: window === '1y' ? -0.25 : 0.1 },
    { symbol: 'AAPL', start: '2025-10-31', change: window === '1y' ? 0.25 : 0.05 },
    { symbol: 'SPAXX', unavailable: 'cash', detail: 'Cash.' },
    { symbol: 'NEWCO', unavailable: 'too_new', detail: 'Its prices begin Aug 25, 2026.' },
  ],
})

const ranges = {
  ranges: [
    { symbol: 'MSFT', low: 400, high: 600, latest: 500 },
    { symbol: 'AAPL', low: 150, high: 250, latest: 200 },
    { symbol: 'SPAXX', unavailable: 'cash' },
    { symbol: 'NEWCO', low: 10, high: 30, latest: 20 },
  ],
}

function fakeApi() {
  const urls: string[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://localhost')
    urls.push(url.pathname + url.search)
    if (url.pathname.endsWith('/ranges')) return new Response(JSON.stringify(ranges))
    const window = url.searchParams.get('window') ?? 'since'
    return new Response(JSON.stringify(changesFor(window)))
  })
  return urls
}

function renderTable(dirty = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <HoldingsTable
          rows={rows}
          holdings={holdings}
          totalValue={5000}
          dirty={dirty}
          onShares={() => {}}
          onRemove={() => {}}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const bodySymbols = () =>
  within(screen.getAllByRole('rowgroup')[1]!)
    .getAllByRole('row')
    .map((row) => within(row).getAllByRole('cell')[0]!.textContent)

afterEach(() => vi.restoreAllMocks())

describe('HoldingsTable', () => {
  it('shows each gain in dollars and percent, N/A with a reason, and the account total', async () => {
    fakeApi()
    renderTable()
    const apple = (await screen.findByRole('link', { name: 'AAPL' })).closest('tr')!
    // $2,000 now after a 25% gain was worth $1,600 a year ago.
    expect(await within(apple).findByText('+$400.00')).toBeInTheDocument()
    expect(within(apple).getByText('+25.00%')).toBeInTheDocument()
    const microsoft = screen.getByRole('link', { name: 'MSFT' }).closest('tr')!
    expect(within(microsoft).getByText('-$500.00')).toBeInTheDocument()
    const cash = screen.getByRole('link', { name: 'SPAXX' }).closest('tr')!
    expect(
      within(cash).getAllByRole('button', { name: 'About No gain or loss: cash' }),
    ).toHaveLength(3)
    const newco = screen.getByRole('link', { name: 'NEWCO' }).closest('tr')!
    expect(
      within(newco).getAllByRole('button', { name: 'About No gain or loss: too new' }),
    ).toHaveLength(2)
    expect(
      within(apple).getByRole('img', { name: /52-week low \$150\.00, high \$250\.00/ }),
    ).toBeInTheDocument()

    const total = screen.getByRole('rowheader', { name: /Account total/ }).closest('tr')!
    expect(within(total).getByText('$5,000.00')).toBeInTheDocument()
    // -$100 against $1,600 + $2,000 + $500 of cash at the start; NEWCO is left out.
    expect(within(total).getByText('-$100.00')).toBeInTheDocument()
    expect(within(total).getByText('-2.44%')).toBeInTheDocument()
  })

  it('sorts by any column, both ways, then back to the saved order', async () => {
    fakeApi()
    renderTable()
    await screen.findByText('+$400.00')
    expect(bodySymbols()).toEqual(['MSFT', 'AAPL', 'SPAXX', 'NEWCO'])

    await userEvent.click(screen.getByRole('button', { name: 'Symbol' }))
    expect(bodySymbols()).toEqual(['AAPL', 'MSFT', 'NEWCO', 'SPAXX'])
    await userEvent.click(screen.getByRole('button', { name: /Symbol/ }))
    expect(bodySymbols()).toEqual(['SPAXX', 'NEWCO', 'MSFT', 'AAPL'])

    await userEvent.click(screen.getByRole('button', { name: 'Value' }))
    expect(bodySymbols()).toEqual(['AAPL', 'MSFT', 'NEWCO', 'SPAXX'])
    await userEvent.click(screen.getByRole('button', { name: /Value/ }))
    expect(bodySymbols()).toEqual(['SPAXX', 'NEWCO', 'MSFT', 'AAPL'])
    await userEvent.click(screen.getByRole('button', { name: /Value/ }))
    expect(bodySymbols()).toEqual(['MSFT', 'AAPL', 'SPAXX', 'NEWCO'])

    // Holdings without a gain go last either way.
    const [firstGain] = screen.getAllByRole('button', { name: 'Gain/loss' })
    await userEvent.click(firstGain!)
    expect(bodySymbols()).toEqual(['AAPL', 'MSFT', 'SPAXX', 'NEWCO'])
    for (const name of ['Shares', 'Price', 'Weight', '52-week range']) {
      await userEvent.click(screen.getByRole('button', { name }))
      expect(screen.getByRole('columnheader', { name: new RegExp(name) })).toHaveAttribute(
        'aria-sort',
        'descending',
      )
    }
  })

  it('measures each column over the window chosen for it, or since a date', async () => {
    const urls = fakeApi()
    renderTable()
    await screen.findByText('+$400.00')
    expect(urls.some((url) => url.includes('window=1y'))).toBe(true)
    expect(urls.some((url) => url.includes('window=1mo'))).toBe(true)

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Window for gain/loss column 1' }),
      '3mo',
    )
    expect(urls.some((url) => url.includes('window=3mo'))).toBe(true)

    await userEvent.selectOptions(
      screen.getByRole('combobox', { name: 'Window for gain/loss column 2' }),
      'pick',
    )
    const start = screen.getByLabelText('Start of gain/loss column 2')
    await userEvent.type(start, '2024-03-15')
    expect(urls.some((url) => url.includes('since=2024-03-15'))).toBe(true)
    expect(localStorage.getItem('pyqxel.gainWindow.1')).toBe('since:2024-03-15')
    // The date box closes once a start is chosen, and the menu names it.
    expect(screen.queryByLabelText('Start of gain/loss column 2')).toBeNull()
    expect(
      screen.getByRole('combobox', { name: 'Window for gain/loss column 2' }),
    ).toHaveDisplayValue('Since 3/15/24')
  })

  it('removes a holding by its own button', async () => {
    fakeApi()
    const removed: string[] = []
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <HoldingsTable
            rows={rows}
            holdings={holdings}
            totalValue={5000}
            dirty={false}
            onShares={() => {}}
            onRemove={(symbol) => removed.push(symbol)}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    await userEvent.click(await screen.findByRole('button', { name: 'Remove MSFT' }))
    expect(removed).toEqual(['MSFT'])
  })

  it('leaves values and the total blank while the holdings are being edited', async () => {
    fakeApi()
    renderTable(true)
    expect(await screen.findByRole('link', { name: 'AAPL' })).toBeInTheDocument()
    expect(screen.queryByText('+$400.00')).toBeNull()
    expect(screen.queryByRole('rowheader', { name: /Account total/ })).toBeNull()
  })
})
