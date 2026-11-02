// Choices for the Portfolio page's holdings table: the windows its two gain/loss columns
// cover, and how its rows are sorted. Each is remembered per browser.

import type { GainWindow } from '../api/types'
import { readStored, writeStored } from './storage'

/** A gain/loss column's window: a preset, or since a chosen day (YYYY-MM-DD). */
export type GainChoice = GainWindow | { since: string }

export const GAIN_WINDOWS: { value: GainWindow; label: string; short: string }[] = [
  { value: '1d', label: '1 day', short: '1D' },
  { value: '1w', label: '1 week', short: '1W' },
  { value: '1mo', label: '1 month', short: '1M' },
  { value: '3mo', label: '3 months', short: '3M' },
  { value: '6mo', label: '6 months', short: '6M' },
  { value: 'ytd', label: 'Year to date', short: 'YTD' },
  { value: '1y', label: '1 year', short: '1Y' },
  { value: '2y', label: '2 years', short: '2Y' },
  { value: '3y', label: '3 years', short: '3Y' },
  { value: '5y', label: '5 years', short: '5Y' },
  { value: '10y', label: '10 years', short: '10Y' },
]

/** The two gain/loss columns' windows before a viewer picks others. */
export const DEFAULT_GAIN_WINDOWS: [GainWindow, GainWindow] = ['1y', '1mo']

const SINCE = 'since:'
const DAY = /^\d{4}-\d{2}-\d{2}$/
const gainKey = (column: number) => `pyqxel.gainWindow.${column}`

/** The window this viewer chose for gain/loss column `column` (0 or 1), or its default. */
export function storedGainChoice(column: 0 | 1): GainChoice {
  const stored = readStored(gainKey(column)) ?? ''
  const preset = GAIN_WINDOWS.find((option) => option.value === stored)
  if (preset) return preset.value
  const since = stored.startsWith(SINCE) ? stored.slice(SINCE.length) : ''
  return DAY.test(since) ? { since } : DEFAULT_GAIN_WINDOWS[column]
}

export function storeGainChoice(column: 0 | 1, choice: GainChoice): void {
  writeStored(gainKey(column), typeof choice === 'string' ? choice : SINCE + choice.since)
}

/** A stable text form of a choice, for query keys. */
export const gainChoiceKey = (choice: GainChoice): string =>
  typeof choice === 'string' ? choice : SINCE + choice.since

/** A custom start, short enough for the column's menu: 3/4/25. */
export function sinceLabel(since: string): string {
  const [year = 1970, month = 1, day = 1] = since.split('-').map(Number)
  return new Date(year, month - 1, day).toLocaleDateString('en-US', {
    month: 'numeric',
    day: 'numeric',
    year: '2-digit',
  })
}

/** Columns the holdings can be sorted by. */
export type HoldingSortKey =
  'symbol' | 'name' | 'shares' | 'price' | 'value' | 'weight' | 'gain0' | 'gain1' | 'range'

export interface HoldingSort {
  key: HoldingSortKey
  descending: boolean
}

const SORT_KEY = 'pyqxel.holdingsSort'
const SORT_KEYS: HoldingSortKey[] = [
  'symbol',
  'name',
  'shares',
  'price',
  'value',
  'weight',
  'gain0',
  'gain1',
  'range',
]

/** The sort this viewer chose, or null for the order the portfolio was saved in. */
export function storedHoldingSort(): HoldingSort | null {
  const [key, direction] = (readStored(SORT_KEY) ?? '').split(':')
  const known = SORT_KEYS.find((option) => option === key)
  return known ? { key: known, descending: direction === 'desc' } : null
}

export function storeHoldingSort(sort: HoldingSort | null): void {
  writeStored(SORT_KEY, sort && `${sort.key}:${sort.descending ? 'desc' : 'asc'}`)
}

/** Text columns start A–Z; number columns start with the largest. */
const TEXT_KEYS: HoldingSortKey[] = ['symbol', 'name']

/**
 * The sort after a column's header is clicked: a new column starts in its natural
 * direction (A–Z, or largest first), a second click reverses it, and a third returns to
 * the saved order.
 */
export function nextHoldingSort(current: HoldingSort | null, key: HoldingSortKey) {
  const first = !TEXT_KEYS.includes(key)
  if (current?.key !== key) return { key, descending: first }
  if (current.descending === first) return { key, descending: !first }
  return null
}

/**
 * `items` in `sort` order, by the value `valueOf` gives each; items without one go last
 * either way, and ties keep their saved order.
 */
export function sortHoldings<T>(
  items: T[],
  sort: HoldingSort | null,
  valueOf: (item: T, key: HoldingSortKey) => number | string | null | undefined,
): T[] {
  if (sort === null) return items
  const direction = sort.descending ? -1 : 1
  return items
    .map((item, index) => ({ item, index, value: valueOf(item, sort.key) }))
    .sort((a, b) => {
      const aMissing = a.value === null || a.value === undefined || a.value === ''
      const bMissing = b.value === null || b.value === undefined || b.value === ''
      if (aMissing || bMissing) return aMissing === bMissing ? a.index - b.index : aMissing ? 1 : -1
      const order =
        typeof a.value === 'string' && typeof b.value === 'string'
          ? a.value.localeCompare(b.value)
          : Number(a.value) - Number(b.value)
      return order === 0 ? a.index - b.index : order * direction
    })
    .map(({ item }) => item)
}
