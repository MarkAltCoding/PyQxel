import { useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getHoldingChanges, getHoldingRanges } from '../api/endpoints'
import type { HoldingChange, HoldingRange, HoldingValue } from '../api/types'
import { money, percent, signedPercent, tone } from '../lib/format'
import type { InfoKey } from '../lib/glossary'
import {
  GAIN_WINDOWS,
  gainChoiceKey,
  nextHoldingSort,
  sinceLabel,
  sortHoldings,
  storedGainChoice,
  storedHoldingSort,
  storeGainChoice,
  storeHoldingSort,
  type GainChoice,
  type HoldingSort,
  type HoldingSortKey,
} from '../lib/holdings'
import { InfoTip } from './InfoTip'
import { PHONE_QUERY, useMediaQuery } from '../lib/useMediaQuery'

/** A row being edited: the symbol and the share count as typed. */
export interface HoldingRow {
  symbol: string
  shares: string
}

/** A holding's gain or loss over a window: dollars and percent, or why there is none. */
type Gain =
  { dollars: number; fraction: number } | { unavailable: InfoKey } | { loading: true } | undefined

const UNAVAILABLE: Record<NonNullable<HoldingChange['unavailable']>, InfoKey> = {
  cash: 'gainUnavailableCash',
  too_new: 'gainUnavailableNew',
  no_data: 'gainUnavailableData',
}

/** The gain of `holding` from its window change: the dollars assume today's shares were
 * held for the whole window, so they are its value now times the change over 1 + change. */
function gainOf(
  holding: HoldingValue | undefined,
  change: HoldingChange | undefined,
  loading: boolean,
): Gain {
  if (!holding) return undefined
  if (holding.cash) return { unavailable: 'gainUnavailableCash' }
  if (!change) return loading ? { loading: true } : undefined
  if (change.unavailable) return { unavailable: UNAVAILABLE[change.unavailable] }
  if (change.change === null || change.change === undefined || holding.value == null) {
    return { unavailable: 'gainUnavailableData' }
  }
  const fraction = change.change
  return { dollars: (holding.value * fraction) / (1 + fraction), fraction }
}

const MAX_SINCE_YEARS = 30

/** A day as the date input takes it: 2025-03-04. */
const isoDay = (day: Date) =>
  `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`

/**
 * The holdings, valued, with each one's gain or loss over two windows the viewer picks,
 * its 52-week range, and the account's total below; laid out like a broker's positions
 * view. Every column sorts.
 */
export function HoldingsTable({
  rows,
  holdings,
  totalValue,
  dirty,
  onShares,
  onRemove,
}: {
  rows: HoldingRow[]
  /** The saved holdings, valued; rows not among them are new and unsaved. */
  holdings: HoldingValue[]
  totalValue: number | undefined
  /** Whether there are unsaved edits, which leave values and weights unknown. */
  dirty: boolean
  onShares: (symbol: string, shares: string) => void
  onRemove: (symbol: string) => void
}) {
  const [choices, setChoices] = useState<[GainChoice, GainChoice]>(() => [
    storedGainChoice(0),
    storedGainChoice(1),
  ])
  const [sort, setSort] = useState<HoldingSort | null>(storedHoldingSort)
  const valued = new Map(holdings.map((holding) => [holding.symbol, holding]))
  const symbols = holdings.map((holding) => holding.symbol)
  const listed = symbols.join(',')

  const first = useQuery({
    queryKey: ['holding-changes', listed, gainChoiceKey(choices[0])],
    queryFn: () => getHoldingChanges(symbols, choices[0]),
    enabled: symbols.length > 0,
    placeholderData: keepPreviousData,
  })
  const second = useQuery({
    queryKey: ['holding-changes', listed, gainChoiceKey(choices[1])],
    queryFn: () => getHoldingChanges(symbols, choices[1]),
    enabled: symbols.length > 0,
    placeholderData: keepPreviousData,
  })
  const ranges = useQuery({
    queryKey: ['holding-ranges', listed],
    queryFn: () => getHoldingRanges(symbols),
    enabled: symbols.length > 0,
  })
  const columns = [first, second] as const
  const changes = columns.map(
    (query) => new Map((query.data?.changes ?? []).map((change) => [change.symbol, change])),
  )
  const rangeOf = new Map((ranges.data?.ranges ?? []).map((range) => [range.symbol, range]))

  // Values and gains are of the saved portfolio, so they are left blank while it is edited.
  const shown = (symbol: string) => (dirty ? undefined : valued.get(symbol))
  const gains = (column: 0 | 1, symbol: string) =>
    gainOf(
      shown(symbol),
      changes[column]?.get(symbol),
      columns[column].isFetching || columns[column].isPlaceholderData,
    )

  const valueOf = (row: HoldingRow, key: HoldingSortKey): number | string | null | undefined => {
    const holding = valued.get(row.symbol)
    switch (key) {
      case 'symbol':
        return row.symbol
      case 'name':
        return holding?.name
      case 'shares':
        return Number(row.shares) || null
      case 'price':
        return holding?.price
      case 'value':
        return shown(row.symbol)?.value
      case 'weight':
        return shown(row.symbol)?.weight
      case 'gain0':
      case 'gain1': {
        const gain = gains(key === 'gain0' ? 0 : 1, row.symbol)
        return gain && 'fraction' in gain ? gain.fraction : null
      }
      case 'range':
        // The range is in the listing's own currency, so is the price it is set against.
        return rangePosition(rangeOf.get(row.symbol), holding?.local_price ?? holding?.price)
    }
  }
  const sorted = sortHoldings(rows, sort, valueOf)

  const sortBy = (key: HoldingSortKey) => {
    const next = nextHoldingSort(sort, key)
    storeHoldingSort(next)
    setSort(next)
  }
  const choose = (column: 0 | 1, choice: GainChoice) => {
    storeGainChoice(column, choice)
    setChoices((current) => (column === 0 ? [choice, current[1]] : [current[0], choice]))
  }

  const header = (key: HoldingSortKey, label: string, className?: string, info?: ReactNode) => (
    <th
      className={className}
      aria-sort={sort?.key === key ? (sort.descending ? 'descending' : 'ascending') : undefined}
    >
      <button type="button" className="sort" onClick={() => sortBy(key)}>
        {label}
        {sort?.key === key ? (sort.descending ? ' ↓' : ' ↑') : ''}
      </button>
      {info}
    </th>
  )

  const showTotal = !dirty && holdings.length > 0
  // On a phone each holding is a card (see "holdings-cards" in global.css), labeled field
  // by field, and the sort and windows the header row holds move into a bar above it.
  const phone = useMediaQuery(PHONE_QUERY)
  const gainLabel = (column: 0 | 1) => `Gain/loss, ${choiceLabel(choices[column])}`
  return (
    <div className={phone ? 'holdings-cards' : 'table-wrap'}>
      {phone && (
        <div className="holdings-phone-controls">
          <label className="field">
            <span>Sort by</span>
            <select
              value={sort ? `${sort.key}:${sort.descending ? 'down' : 'up'}` : ''}
              onChange={(event) => {
                const [key, direction] = event.target.value.split(':') as [HoldingSortKey, string]
                const next = event.target.value ? { key, descending: direction === 'down' } : null
                storeHoldingSort(next)
                setSort(next)
              }}
            >
              <option value="">As entered</option>
              {PHONE_SORTS.map(([key, label]) => (
                <optgroup key={key} label={label}>
                  <option value={`${key}:up`}>
                    {label}, {ascendingWord(key)}
                  </option>
                  <option value={`${key}:down`}>
                    {label}, {descendingWord(key)}
                  </option>
                </optgroup>
              ))}
            </select>
          </label>
          {([0, 1] as const).map((column) => (
            <div className="field" key={column}>
              <span className="heading-with-info">
                Gain/loss window {column + 1}
                <InfoTip topic="holdingGainLoss" />
              </span>
              <WindowPicker
                column={column}
                choice={choices[column]}
                onChange={(choice) => choose(column, choice)}
              />
            </div>
          ))}
        </div>
      )}
      <table className="data holdings-table">
        <thead className={phone ? 'visually-hidden' : undefined}>
          <tr>
            {header('symbol', 'Symbol')}
            {header('name', 'Name')}
            {header('shares', 'Shares', 'num')}
            {header('price', 'Price', 'num')}
            {header('value', 'Value', 'num')}
            {header('weight', 'Weight', 'num', <InfoTip topic="holdingWeight" />)}
            {([0, 1] as const).map((column) => (
              <th
                key={column}
                className="num"
                aria-sort={
                  sort?.key === `gain${column}`
                    ? sort.descending
                      ? 'descending'
                      : 'ascending'
                    : undefined
                }
              >
                <div className="gain-header">
                  <span>
                    <button
                      type="button"
                      className="sort"
                      onClick={() => sortBy(column === 0 ? 'gain0' : 'gain1')}
                    >
                      Gain/loss
                      {sort?.key === `gain${column}` ? (sort.descending ? ' ↓' : ' ↑') : ''}
                    </button>
                    <InfoTip topic="holdingGainLoss" />
                  </span>
                  <WindowPicker
                    column={column}
                    choice={choices[column]}
                    onChange={(choice) => choose(column, choice)}
                  />
                </div>
              </th>
            ))}
            {header('range', '52-week range', 'range-col', <InfoTip topic="holding52WeekRange" />)}
            <th>
              <span className="visually-hidden">Remove</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const known = valued.get(row.symbol)
            const holding = shown(row.symbol)
            return (
              <tr key={row.symbol}>
                <td className="symbol-col">
                  <Link to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.symbol}</Link>
                </td>
                <td className="muted name-col" title={known?.name ?? undefined}>
                  {known?.name ?? ''}
                  {known?.cash && (
                    <span className="heading-with-info" style={{ marginLeft: 6 }}>
                      <span className="badge">Cash</span>
                      <InfoTip topic="moneyMarketCash" />
                    </span>
                  )}
                </td>
                <td className="num shares-col" data-label="Shares">
                  <input
                    type="number"
                    min="0"
                    step="any"
                    aria-label={`Shares of ${row.symbol}`}
                    style={{ width: 84, textAlign: 'right' }}
                    value={row.shares}
                    aria-invalid={
                      row.shares.trim() === '' || !(Number(row.shares) >= 0) ? true : undefined
                    }
                    title={
                      row.shares.trim() !== '' && Number(row.shares) === 0
                        ? `0 shares: saving removes ${row.symbol}`
                        : undefined
                    }
                    onChange={(event) => onShares(row.symbol, event.target.value)}
                  />
                </td>
                <td className="num price-col" data-label="Price">
                  {money(known?.price)}
                  {known?.local_price != null && known.currency && (
                    <div
                      className="stat-detail"
                      title="The price where it trades, converted to dollars at the latest reference rate"
                    >
                      {money(known.local_price, known.currency)}
                    </div>
                  )}
                </td>
                <td className="num value-col" data-label="Value">
                  {money(holding?.value)}
                </td>
                <td className="num weight-col" data-label="Weight">
                  <WeightBar weight={holding?.weight} />
                </td>
                <td className="num gain-col" data-label={gainLabel(0)}>
                  <GainCell gain={gains(0, row.symbol)} />
                </td>
                <td className="num gain-col" data-label={gainLabel(1)}>
                  <GainCell gain={gains(1, row.symbol)} />
                </td>
                <td className="range-col" data-label="52-week range">
                  {known?.cash ? (
                    <Unavailable topic="gainUnavailableCash" />
                  ) : (
                    <RangeBar
                      symbol={row.symbol}
                      range={rangeOf.get(row.symbol)}
                      price={known?.local_price ?? known?.price}
                      currency={known?.local_price != null ? (known.currency ?? 'USD') : 'USD'}
                      loading={ranges.isLoading && symbols.includes(row.symbol)}
                    />
                  )}
                </td>
                <td className="remove-col">
                  <button
                    type="button"
                    className="button ghost small danger remove-holding"
                    aria-label={`Remove ${row.symbol}`}
                    title={`Remove ${row.symbol}`}
                    onClick={() => onRemove(row.symbol)}
                  >
                    <span className="remove-mark" aria-hidden="true">
                      ✕
                    </span>
                  </button>
                </td>
              </tr>
            )
          })}
        </tbody>
        {showTotal && (
          <tfoot>
            <tr className="total-row">
              <th scope="row" colSpan={4}>
                <span className="heading-with-info">
                  Account total
                  <InfoTip topic="accountTotal" />
                </span>
              </th>
              <td className="num value-col" data-label="Value">
                {money(totalValue)}
              </td>
              <td className="num weight-col">{totalValue ? percent(1, 0) : ''}</td>
              {([0, 1] as const).map((column) => (
                <td key={column} className="num gain-col" data-label={gainLabel(column)}>
                  <GainCell
                    gain={totalGain(
                      holdings,
                      holdings.map((h) => gains(column, h.symbol)),
                    )}
                  />
                </td>
              ))}
              <td colSpan={2} className="filler-col" />
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}

/** The holdings' gains added up, and as a percent of what they were worth at the window's
 * start: each holding its value now less its gain, and cash, which has no gain, its value
 * now. Holdings without a gain (too new, or no prices) are left out of both. */
function totalGain(holdings: HoldingValue[], gains: Gain[]): Gain {
  if (gains.some((gain) => gain && 'loading' in gain)) return { loading: true }
  let dollars = 0
  let start = 0
  let measured = 0
  holdings.forEach((holding, index) => {
    const gain = gains[index]
    if (gain && 'dollars' in gain && holding.value != null) {
      dollars += gain.dollars
      start += holding.value - gain.dollars
      measured += 1
    } else if (holding.cash && holding.value != null) {
      start += holding.value
    }
  })
  if (measured === 0) return undefined
  return { dollars, fraction: start > 0 ? dollars / start : 0 }
}

function signedMoney(value: number): string {
  if (Math.abs(value) < 0.005) return money(0)
  return `${value > 0 ? '+' : '-'}${money(Math.abs(value))}`
}

function GainCell({ gain }: { gain: Gain }) {
  if (gain === undefined) return <span className="muted">—</span>
  if ('loading' in gain) return <span className="muted">…</span>
  if ('unavailable' in gain) return <Unavailable topic={gain.unavailable} />
  const direction = tone(gain.dollars)
  return (
    <span className={`gain-cell ${direction ?? ''}`}>
      <span>{signedMoney(gain.dollars)}</span>
      <span className="gain-percent">{signedPercent(gain.fraction, 2)}</span>
    </span>
  )
}

/** "N/A", with an "i" saying why: it fits in the space a number would take. */
function Unavailable({ topic }: { topic: InfoKey }) {
  return (
    <span className="unavailable">
      N/A
      <InfoTip topic={topic} />
    </span>
  )
}

/** Where `price` sits in the 52-week range, from 0 at the low to 1 at the high. */
function rangePosition(range: HoldingRange | undefined, price: number | null | undefined) {
  if (!range || range.low == null || range.high == null) return null
  const latest = price ?? range.latest
  if (latest == null) return null
  const low = Math.min(range.low, latest)
  const high = Math.max(range.high, latest)
  return high > low ? (latest - low) / (high - low) : 0.5
}

/** The 52-week low and high at the ends of a line, with a dot at the latest price. */
function RangeBar({
  symbol,
  range,
  price,
  currency,
  loading,
}: {
  symbol: string
  range: HoldingRange | undefined
  /** The latest price, in ``currency``: the listing's own, as the range is. */
  price: number | null | undefined
  currency: string
  loading: boolean
}) {
  if (loading) return <span className="muted">…</span>
  if (!range) return <span className="muted">—</span>
  if (range.unavailable || range.low == null || range.high == null) {
    return <Unavailable topic="gainUnavailableData" />
  }
  const latest = price ?? range.latest ?? range.low
  const low = Math.min(range.low, latest)
  const high = Math.max(range.high, latest)
  const position = rangePosition(range, price) ?? 0.5
  return (
    <span
      className="range-bar"
      role="img"
      aria-label={`${symbol} 52-week low ${money(low, currency)}, high ${money(high, currency)}; the latest price, ${money(latest, currency)}, is ${percent(position, 0)} of the way from low to high.`}
    >
      <span className="range-end">{money(low, currency)}</span>
      <span className="range-track" aria-hidden="true">
        <span className="range-dot" style={{ left: `${position * 100}%` }} />
      </span>
      <span className="range-end">{money(high, currency)}</span>
    </span>
  )
}

function WeightBar({ weight }: { weight: number | null | undefined }) {
  if (weight === null || weight === undefined) return <span className="muted">—</span>
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
      <span
        aria-hidden="true"
        style={{
          display: 'inline-block',
          width: 48,
          height: 6,
          borderRadius: 3,
          background: 'var(--surface-sunken)',
          overflow: 'hidden',
        }}
      >
        <span
          style={{
            display: 'block',
            width: `${Math.min(100, weight * 100)}%`,
            height: '100%',
            borderRadius: 3,
            background: 'var(--series-1)',
          }}
        />
      </span>
      <span style={{ minWidth: 44, textAlign: 'right' }}>{percent(weight)}</span>
    </span>
  )
}

/** What a phone's sort menu offers, in the order the table's columns come. */
const PHONE_SORTS: [HoldingSortKey, string][] = [
  ['symbol', 'Symbol'],
  ['name', 'Name'],
  ['shares', 'Shares'],
  ['price', 'Price'],
  ['value', 'Value'],
  ['weight', 'Weight'],
  ['gain0', 'Gain/loss 1'],
  ['gain1', 'Gain/loss 2'],
  ['range', '52-week range'],
]

const ascendingWord = (key: HoldingSortKey) =>
  key === 'symbol' || key === 'name' ? 'A to Z' : 'lowest first'
const descendingWord = (key: HoldingSortKey) =>
  key === 'symbol' || key === 'name' ? 'Z to A' : 'highest first'

/** A gain/loss window as a phone's card names it, such as "1 month" or "since Mar 3, 2024". */
function choiceLabel(choice: GainChoice): string {
  if (typeof choice !== 'string') return `since ${sinceLabel(choice.since)}`
  return GAIN_WINDOWS.find((option) => option.value === choice)?.label.toLowerCase() ?? choice
}

const SINCE_OPTION = 'since'
const PICK_OPTION = 'pick'

/** A gain/loss column's window: a preset, or a custom start picked on a calendar. */
function WindowPicker({
  column,
  choice,
  onChange,
}: {
  column: 0 | 1
  choice: GainChoice
  onChange: (choice: GainChoice) => void
}) {
  // A date box shows only while a custom start is being picked; once one is, the menu
  // names it, and offers picking another.
  const [picking, setPicking] = useState(false)
  // The latest and earliest starts a custom window may have: yesterday, and 30 years ago.
  const [[earliest, yesterday]] = useState(() => {
    const today = new Date()
    return [
      isoDay(new Date(today.getFullYear() - MAX_SINCE_YEARS, today.getMonth(), 1)),
      isoDay(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1)),
    ] as const
  })
  const since = typeof choice === 'string' ? '' : choice.since
  return (
    <span className="window-picker">
      <select
        aria-label={`Window for gain/loss column ${column + 1}`}
        value={picking ? PICK_OPTION : typeof choice === 'string' ? choice : SINCE_OPTION}
        onChange={(event) => {
          const next = event.target.value
          setPicking(next === PICK_OPTION)
          if (next === PICK_OPTION || next === SINCE_OPTION) return
          onChange(next as GainChoice & string)
        }}
      >
        {GAIN_WINDOWS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
        {since && <option value={SINCE_OPTION}>Since {sinceLabel(since)}</option>}
        <option value={PICK_OPTION}>{since ? 'Another start date…' : 'Since a date…'}</option>
      </select>
      {picking && (
        <input
          type="date"
          aria-label={`Start of gain/loss column ${column + 1}`}
          min={earliest}
          max={yesterday}
          value={since}
          onChange={(event) => {
            const day = event.target.value
            if (/^\d{4}-\d{2}-\d{2}$/.test(day) && day >= earliest && day <= yesterday) {
              setPicking(false)
              onChange({ since: day })
            }
          }}
        />
      )}
    </span>
  )
}
