import { useState } from 'react'
import { useNavigate } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { getUniverseStatus, screen } from '../api/endpoints'
import type { RankedStock, ScreenField, ScreenFilter, ScreenRequest } from '../api/types'
import { InfoTip } from '../components/InfoTip'
import { ErrorNotice, Notice } from '../components/Notice'
import type { InfoKey } from '../lib/glossary'
import { compact, dateTime, money, number, percent, shortDate } from '../lib/format'
import { readStored, writeStored } from '../lib/storage'

type Kind = 'percent' | 'ratio' | 'money' | 'compact' | 'beta'

/** Every screenable metric: how to label it, show it, and enter bounds for it. */
const FIELDS: Record<
  ScreenField,
  { label: string; info: InfoKey; kind: Kind; descending: boolean }
> = {
  market_cap: { label: 'Market cap', info: 'marketCap', kind: 'compact', descending: true },
  price: { label: 'Price', info: 'price', kind: 'money', descending: true },
  avg_dollar_volume: {
    label: 'Avg. daily $ volume',
    info: 'avgDollarVolume',
    kind: 'compact',
    descending: true,
  },
  pe_ratio: { label: 'P/E', info: 'pe', kind: 'ratio', descending: false },
  price_to_sales: { label: 'Price / sales', info: 'priceSales', kind: 'ratio', descending: false },
  ev_to_ebitda: { label: 'EV / EBITDA', info: 'evEbitda', kind: 'ratio', descending: false },
  fcf_yield: { label: 'FCF yield', info: 'fcfYield', kind: 'percent', descending: true },
  gross_margin: { label: 'Gross margin', info: 'grossMargin', kind: 'percent', descending: true },
  operating_margin: {
    label: 'Operating margin',
    info: 'operatingMargin',
    kind: 'percent',
    descending: true,
  },
  net_margin: { label: 'Net margin', info: 'netMargin', kind: 'percent', descending: true },
  return_on_equity: { label: 'Return on equity', info: 'roe', kind: 'percent', descending: true },
  revenue_growth: {
    label: 'Revenue growth',
    info: 'revenueGrowth',
    kind: 'percent',
    descending: true,
  },
  earnings_growth: {
    label: 'Earnings growth',
    info: 'earningsGrowth',
    kind: 'percent',
    descending: true,
  },
  return_1m: { label: '1-month return', info: 'return1m', kind: 'percent', descending: true },
  return_6m: { label: '6-month return', info: 'return6m', kind: 'percent', descending: true },
  momentum_12_1: {
    label: 'Momentum (12–1)',
    info: 'momentum12_1',
    kind: 'percent',
    descending: true,
  },
  volatility: { label: 'Volatility', info: 'volatility', kind: 'percent', descending: false },
  max_drawdown: { label: 'Max drawdown', info: 'maxDrawdown', kind: 'percent', descending: true },
  beta_market: { label: 'Market beta', info: 'marketBeta', kind: 'beta', descending: false },
  beta_size: { label: 'Size beta', info: 'sizeBeta', kind: 'beta', descending: true },
  beta_value: { label: 'Value beta', info: 'valueBeta', kind: 'beta', descending: true },
  beta_momentum: { label: 'Momentum beta', info: 'momentumBeta', kind: 'beta', descending: true },
  factor_r_squared: {
    label: 'Factor R²',
    info: 'factorRSquared',
    kind: 'percent',
    descending: false,
  },
}

const FIELD_NAMES = Object.keys(FIELDS) as ScreenField[]

const UNIVERSES = [
  { value: 'all', label: 'All stocks' },
  { value: 'large_cap', label: 'Large cap (top 500)' },
  { value: 'broad_market', label: 'Broad market (top 3,000)' },
  { value: 'liquid', label: 'Liquid ($5M+ a day)' },
] as const

const BASE_COLUMNS: ScreenField[] = [
  'market_cap',
  'pe_ratio',
  'fcf_yield',
  'revenue_growth',
  'momentum_12_1',
  'volatility',
  'beta_market',
]

const PAGE_SIZE = 50
const STORAGE_KEY = 'pyqxel.screen'

function show(field: ScreenField, value: number | null | undefined): string {
  switch (FIELDS[field].kind) {
    case 'percent':
      return percent(value)
    case 'money':
      return money(value)
    case 'compact':
      return compact(value)
    case 'ratio':
      return number(value, 1)
    default:
      return number(value)
  }
}

/** Percent metrics are entered as percentages (15 = 15%) and sent as decimals. */
const toApi = (field: ScreenField, text: string): number | null => {
  if (text.trim() === '') return null
  const value = Number(text)
  if (!Number.isFinite(value)) return null
  return FIELDS[field].kind === 'percent' ? value / 100 : value
}

interface FilterRow {
  field: ScreenField
  min: string
  max: string
}

interface Screen {
  universe: NonNullable<ScreenRequest['universe']>
  sector: string
  filters: FilterRow[]
  sort: ScreenField
  descending: boolean
}

const DEFAULT_SCREEN: Screen = {
  universe: 'large_cap',
  sector: '',
  filters: [],
  sort: 'market_cap',
  descending: true,
}

function loadScreen(): Screen {
  try {
    const stored = JSON.parse(readStored(STORAGE_KEY) ?? 'null') as Screen | null
    if (stored && stored.sort in FIELDS) return { ...DEFAULT_SCREEN, ...stored }
  } catch {
    // Ignore a corrupt or outdated saved screen.
  }
  return DEFAULT_SCREEN
}

export function ScreenerPage() {
  const navigate = useNavigate()
  const [state, setState] = useState<Screen>(loadScreen)
  const [page, setPage] = useState(0)
  const update = (next: Partial<Screen>) => {
    const merged = { ...state, ...next }
    setState(merged)
    setPage(0)
    writeStored(STORAGE_KEY, JSON.stringify(merged))
  }

  const status = useQuery({ queryKey: ['universe-status'], queryFn: getUniverseStatus })
  const filters: ScreenFilter[] = state.filters
    .map((row) => ({
      field: row.field,
      min: toApi(row.field, row.min),
      max: toApi(row.field, row.max),
    }))
    .filter((row) => row.min !== null || row.max !== null)
  const request: ScreenRequest = {
    universe: state.universe,
    sectors: state.sector ? [state.sector] : [],
    filters,
    sort: { field: state.sort, descending: state.descending },
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  const results = useQuery({
    queryKey: ['screen', request],
    queryFn: () => screen(request),
    placeholderData: keepPreviousData,
  })

  const columns = [
    ...BASE_COLUMNS,
    ...[state.sort, ...state.filters.map((f) => f.field)].filter(
      (field, index, all) => !BASE_COLUMNS.includes(field) && all.indexOf(field) === index,
    ),
  ]
  const total = results.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const sortBy = (field: ScreenField) =>
    update(
      field === state.sort
        ? { descending: !state.descending }
        : { sort: field, descending: FIELDS[field].descending },
    )

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Screener</h1>
          <div className="subtitle">
            {status.data
              ? `${status.data.stocks.toLocaleString()} US stocks · prices as of ${shortDate(status.data.prices_as_of)} · refreshed ${dateTime(status.data.refreshed_at)}`
              : 'US-listed common stocks, refreshed after each trading day'}
          </div>
        </div>
      </header>

      {status.data?.last_error && (
        <Notice tone="warning">The latest refresh failed: {status.data.last_error}</Notice>
      )}
      {status.data && status.data.stocks === 0 && (
        <Notice>
          The universe is empty. Run <code>python -m app.jobs.screener</code> on the server to build
          it.
        </Notice>
      )}

      <section className="card stack" style={{ gap: 14 }}>
        <div className="filter-row">
          <label className="field">
            <span>Universe</span>
            <select
              value={state.universe}
              onChange={(event) => update({ universe: event.target.value as Screen['universe'] })}
            >
              {UNIVERSES.map((u) => (
                <option key={u.value} value={u.value}>
                  {u.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Sector</span>
            <select
              value={state.sector}
              onChange={(event) => update({ sector: event.target.value })}
            >
              <option value="">All sectors</option>
              {(status.data?.sectors ?? []).map((sector) => (
                <option key={sector} value={sector}>
                  {sector}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Add a filter</span>
            <select
              value=""
              onChange={(event) => {
                const field = event.target.value as ScreenField
                if (field) update({ filters: [...state.filters, { field, min: '', max: '' }] })
              }}
            >
              <option value="">Choose a metric…</option>
              {FIELD_NAMES.filter((f) => !state.filters.some((row) => row.field === f)).map((f) => (
                <option key={f} value={f}>
                  {FIELDS[f].label}
                </option>
              ))}
            </select>
          </label>
          {(state.filters.length > 0 || state.sector) && (
            <button
              type="button"
              className="button ghost"
              onClick={() => update({ filters: [], sector: '' })}
            >
              Clear filters
            </button>
          )}
        </div>
        {state.filters.length > 0 && (
          <div className="filter-row">
            {state.filters.map((row, index) => {
              const unit = FIELDS[row.field].kind === 'percent' ? ' (%)' : ''
              const change = (key: 'min' | 'max', value: string) =>
                update({
                  filters: state.filters.map((r, i) => (i === index ? { ...r, [key]: value } : r)),
                })
              return (
                <fieldset
                  key={row.field}
                  className="stat"
                  style={{ border: 0, margin: 0, display: 'flex', gap: 8, alignItems: 'flex-end' }}
                >
                  <legend className="visually-hidden">{FIELDS[row.field].label}</legend>
                  <InfoTip topic={FIELDS[row.field].info} />
                  <label className="field" style={{ width: 96 }}>
                    <span>
                      {FIELDS[row.field].label} min{unit}
                    </span>
                    <input
                      type="number"
                      step="any"
                      value={row.min}
                      onChange={(event) => change('min', event.target.value)}
                    />
                  </label>
                  <label className="field" style={{ width: 96 }}>
                    <span>max{unit}</span>
                    <input
                      type="number"
                      step="any"
                      value={row.max}
                      onChange={(event) => change('max', event.target.value)}
                    />
                  </label>
                  <button
                    type="button"
                    className="button ghost small"
                    aria-label={`Remove the ${FIELDS[row.field].label} filter`}
                    onClick={() => update({ filters: state.filters.filter((_, i) => i !== index) })}
                  >
                    ✕
                  </button>
                </fieldset>
              )
            })}
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <div>
            <h2>{total.toLocaleString()} matches</h2>
            <div className="subtitle">
              Sorted by {FIELDS[state.sort].label.toLowerCase()},{' '}
              {state.descending ? 'highest' : 'lowest'} first · click a column to sort
            </div>
          </div>
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <button
              type="button"
              className="button small"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              Previous
            </button>
            <span className="muted" style={{ fontSize: '0.85rem' }}>
              Page {page + 1} of {pages}
            </span>
            <button
              type="button"
              className="button small"
              disabled={page + 1 >= pages}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </div>
        </div>
        {results.error !== null && <ErrorNotice error={results.error} />}
        <div className={`table-wrap${results.isPlaceholderData ? ' card-body refreshing' : ''}`}>
          <table className="data">
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Symbol</th>
                <th>Name</th>
                <th>Sector</th>
                {columns.map((field) => (
                  <th
                    key={field}
                    className="num"
                    aria-sort={
                      field === state.sort
                        ? state.descending
                          ? 'descending'
                          : 'ascending'
                        : undefined
                    }
                  >
                    <button type="button" className="sort" onClick={() => sortBy(field)}>
                      {FIELDS[field].label}
                      {field === state.sort ? (state.descending ? ' ↓' : ' ↑') : ''}
                    </button>
                    <InfoTip topic={FIELDS[field].info} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {results.data?.items.map((stock: RankedStock) => (
                <tr
                  key={stock.symbol}
                  className="clickable"
                  onClick={() => navigate(`/stocks/${encodeURIComponent(stock.symbol)}`)}
                >
                  <td className="num muted">{stock.rank}</td>
                  <td>
                    <a
                      href={`/stocks/${encodeURIComponent(stock.symbol)}`}
                      onClick={(event) => {
                        event.preventDefault()
                        event.stopPropagation()
                        navigate(`/stocks/${encodeURIComponent(stock.symbol)}`)
                      }}
                    >
                      {stock.symbol}
                    </a>
                  </td>
                  <td style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {stock.name}
                  </td>
                  <td className="muted">{stock.sector ?? '—'}</td>
                  {columns.map((field) => (
                    <td key={field} className="num">
                      {show(field, stock[field])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {results.data && results.data.items.length === 0 && (
          <div className="empty">No stocks match these filters.</div>
        )}
      </section>
    </div>
  )
}
