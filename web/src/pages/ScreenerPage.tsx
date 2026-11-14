import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import {
  getLatestPicks,
  getNightlyRefresh,
  getPicks,
  getUniverseStatus,
  screen,
} from '../api/endpoints'
import type { RankedStock, ScreenField, ScreenFilter, ScreenRequest } from '../api/types'
import { InfoTip } from '../components/InfoTip'
import { ErrorNotice, Notice } from '../components/Notice'
import { ScreenerTabs } from '../components/ScreenerTabs'
import { LATEST_PICKS_KEY } from '../lib/picks'
import type { InfoKey } from '../lib/glossary'
import { compact, dateTime, money, number, percent, shortDate } from '../lib/format'
import { NIGHTLY_QUERY_KEY } from '../lib/nightlyRefresh'
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

/** The metrics of a price, which apply to an ETF as to a stock; the rest are a company's,
 * which an ETF does not have (as the API's ETF_FIELDS). */
const ETF_FIELDS = new Set<ScreenField>([
  'price',
  'avg_dollar_volume',
  'return_1m',
  'return_6m',
  'momentum_12_1',
  'volatility',
  'max_drawdown',
  'beta_market',
  'beta_size',
  'beta_value',
  'beta_momentum',
  'factor_r_squared',
])

const ASSET_TYPES = [
  { value: '', label: 'Stocks and ETFs' },
  { value: 'stock', label: 'Stocks only' },
  { value: 'etf', label: 'ETFs only' },
] as const

/** The universe choice that keeps only the securities Claude picked. */
const PICKS = 'picks'

const UNIVERSES = [
  { value: 'sp500', label: 'S&P 500' },
  { value: 'nasdaq', label: 'Listed on Nasdaq' },
  { value: 'all', label: 'Everything' },
  { value: 'large_cap', label: 'Large cap (top 500 stocks)' },
  { value: 'broad_market', label: 'Broad market (top 3,000 stocks)' },
  { value: 'liquid', label: 'Liquid ($5M+ a day)' },
  { value: PICKS, label: 'Claude’s picks' },
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
  /** A part of the universe, or only the securities Claude picked. */
  universe: NonNullable<ScreenRequest['universe']> | typeof PICKS
  sector: string
  /** Stocks, ETFs, or both when empty. */
  assetType: '' | 'stock' | 'etf'
  filters: FilterRow[]
  sort: ScreenField
  descending: boolean
}

const DEFAULT_SCREEN: Screen = {
  universe: 'sp500',
  sector: '',
  assetType: '',
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
  // A link from Claude's picks (?picks=latest, or a stored list's ID) opens the screener
  // on those picks alone.
  const [params] = useSearchParams()
  const linkedPicks = params.get('picks')
  const [state, setState] = useState<Screen>(() =>
    linkedPicks ? { ...loadScreen(), universe: PICKS, sector: '', filters: [] } : loadScreen(),
  )
  const picksId = linkedPicks && linkedPicks !== 'latest' ? linkedPicks : null
  const [page, setPage] = useState(0)
  const update = (next: Partial<Screen>) => {
    const merged = { ...state, ...next }
    setState(merged)
    setPage(0)
    writeStored(STORAGE_KEY, JSON.stringify(merged))
  }

  const status = useQuery({ queryKey: ['universe-status'], queryFn: getUniverseStatus })
  const nightly = useQuery({ queryKey: NIGHTLY_QUERY_KEY, queryFn: getNightlyRefresh })
  const latestRun = nightly.data?.latest_run
  const onlyPicks = state.universe === PICKS
  const picks = useQuery({
    queryKey: picksId ? ['picks', picksId] : LATEST_PICKS_KEY,
    queryFn: () => (picksId ? getPicks(picksId) : getLatestPicks()),
    enabled: onlyPicks,
  })
  const picked = picks.data?.report.picks.map((pick) => pick.symbol) ?? []
  const filters: ScreenFilter[] = state.filters
    .map((row) => ({
      field: row.field,
      min: toApi(row.field, row.min),
      max: toApi(row.field, row.max),
    }))
    .filter((row) => row.min !== null || row.max !== null)
  const request: ScreenRequest = {
    // Claude may pick from anywhere in the screener, so its picks are looked for in all of it.
    universe: state.universe === PICKS ? 'all' : state.universe,
    symbols: onlyPicks ? picked : [],
    sectors: state.sector ? [state.sector] : [],
    asset_types: state.assetType ? [state.assetType] : [],
    filters,
    sort: { field: state.sort, descending: state.descending },
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  }
  // An empty universe has nothing to screen; the notice above the screen says so, once.
  const empty = status.data?.stocks === 0
  const results = useQuery({
    queryKey: ['screen', request],
    queryFn: () => screen(request),
    placeholderData: keepPreviousData,
    enabled: !empty && (!onlyPicks || picked.length > 0),
  })
  // Without picks there is nothing to list, whatever an earlier screen showed.
  const shown = onlyPicks && picked.length === 0 ? undefined : results.data

  const columns = [
    ...BASE_COLUMNS,
    ...[state.sort, ...state.filters.map((f) => f.field)].filter(
      (field, index, all) => !BASE_COLUMNS.includes(field) && all.indexOf(field) === index,
    ),
  ]
  const total = shown?.total ?? 0
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
            {status.data && !empty
              ? `${status.data.own ? 'Your own universe · ' : ''}${status.data.stocks.toLocaleString()} ${status.data.own ? 'US stocks and ETFs' : 'US stocks'} · prices as of ${shortDate(status.data.prices_as_of)} · refreshed ${dateTime(status.data.refreshed_at)}`
              : 'US-listed common stocks, refreshed after each trading day'}
            {status.data?.own && <InfoTip topic="nightlyRefresh" />}
          </div>
        </div>
        <ScreenerTabs />
      </header>

      {status.data?.last_error && (
        <Notice tone="warning">The latest refresh failed: {status.data.last_error}</Notice>
      )}
      {latestRun?.status === 'running' && (
        <Notice>
          Your nightly refresh is running, since {dateTime(latestRun.started_at)}. Until it
          finishes, this shows your last universe.
        </Notice>
      )}
      {latestRun?.status === 'stopped' && (
        <Notice>
          Your latest nightly refresh was stopped partway, so this shows your last universe.{' '}
          <Link to="/settings#nightly-refresh">See why in Settings</Link>.
        </Notice>
      )}
      {latestRun?.status === 'failed' && (
        <Notice tone="warning">
          Your latest nightly refresh failed: {latestRun.error}{' '}
          <Link to="/settings#nightly-refresh">Check it in Settings</Link>.
        </Notice>
      )}
      {empty &&
        (nightly.data?.available && !nightly.data.enabled ? (
          <Notice>
            Build your own screener of every US stock and ETF, to filter and rank by valuation,
            growth, momentum, and risk: turn on the{' '}
            <Link to="/settings#nightly-refresh">nightly screener refresh</Link> in Settings. It
            runs on your Finnhub key, whose free plan is enough, and the screener fills in after its
            first night.
          </Notice>
        ) : nightly.data?.enabled ? (
          <Notice>
            Your nightly screener refresh is on
            {nightly.data.next_run_at
              ? `, and first runs ${dateTime(nightly.data.next_run_at)}`
              : ''}
            . The screener fills in once it has finished, an hour or two after it starts.
          </Notice>
        ) : (
          <Notice>
            The stock screener is not available on this server yet. Once it is, it lists US stocks
            you can filter and rank by their valuation, growth, momentum, and risk.
          </Notice>
        ))}

      <section id="screen" className="card stack" style={{ gap: 14 }}>
        <div className="filter-row">
          <label className="field">
            <span className="heading-with-info">
              Universe
              <InfoTip topic="picksUniverse" />
            </span>
            <select
              aria-label="Universe"
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
            <span>Kind</span>
            <select
              value={state.assetType}
              onChange={(event) => update({ assetType: event.target.value as Screen['assetType'] })}
            >
              {ASSET_TYPES.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
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

      <section id="matches" className="card">
        <div className="card-header">
          <div>
            <h2>{total.toLocaleString()} matches</h2>
            <div className="subtitle">
              Sorted by {FIELDS[state.sort].label.toLowerCase()},{' '}
              {state.descending ? 'highest' : 'lowest'} first · click a column to sort
              {onlyPicks &&
                picks.data &&
                ` · the ${picked.length} securities Claude picked on ${shortDate(picks.data.generated_at)} to hold for ${picks.data.context.horizon}${total < picked.length ? `, of which ${total} ${total === 1 ? 'is' : 'are'} in the screener and pass these filters` : ''}`}
              {state.universe === 'sp500' &&
                ' · S&P 500 companies as of the latest holdings report of IVV, a fund that tracks the index, so a company that joined in the last few months may be missing'}
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
        {results.error !== null && !empty && <ErrorNotice error={results.error} />}
        {picks.error !== null && onlyPicks && <ErrorNotice error={picks.error} />}
        {onlyPicks && picks.data === null && (
          <Notice>
            Claude has not made any picks for you yet.{' '}
            <Link to="/screener/picks">Get Claude’s picks</Link>, and they can be screened here.
          </Notice>
        )}
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
              {shown?.items.map((stock: RankedStock, index, items) => {
                // A company's share classes, such as GOOGL and GOOG, come together with
                // one rank: its number, name, and sector are shown once, beside them all.
                const first = items[index - 1]?.rank !== stock.rank
                const classes = first ? items.filter((item) => item.rank === stock.rank).length : 0
                const open = () => navigate(`/stocks/${encodeURIComponent(stock.symbol)}`)
                return (
                  <tr
                    key={stock.symbol}
                    className={`clickable${first ? '' : ' share-class'}`}
                    onClick={open}
                  >
                    {first && (
                      <td className="num muted" rowSpan={classes}>
                        {stock.rank}
                      </td>
                    )}
                    <td className="symbol">
                      <a
                        href={`/stocks/${encodeURIComponent(stock.symbol)}`}
                        onClick={(event) => {
                          event.preventDefault()
                          event.stopPropagation()
                          open()
                        }}
                      >
                        {stock.symbol}
                      </a>
                    </td>
                    {first && (
                      <td
                        rowSpan={classes}
                        style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}
                      >
                        {stock.name}
                      </td>
                    )}
                    {first && (
                      <td className="muted" rowSpan={classes}>
                        {stock.asset_type === 'etf' ? (
                          <span className="badge">ETF</span>
                        ) : (
                          (stock.sector ?? '—')
                        )}
                      </td>
                    )}
                    {columns.map((field) => (
                      <td key={field} className="num">
                        {stock.asset_type === 'etf' && !ETF_FIELDS.has(field) ? (
                          <span
                            className="muted"
                            title="Does not apply to an ETF, which is not a company"
                          >
                            n/a
                          </span>
                        ) : (
                          show(field, stock[field])
                        )}
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {shown?.items.some((stock, index, items) => items[index - 1]?.rank === stock.rank) && (
          <p className="disclaimer heading-with-info" style={{ marginTop: 12 }}>
            <span>
              Symbols that share a number are share classes of one company, such as Alphabet&apos;s
              GOOGL and GOOG, counted as one match.
            </span>
            <InfoTip topic="shareClasses" />
          </p>
        )}
        {shown?.items.some((stock) => stock.asset_type === 'etf') && (
          <p className="disclaimer heading-with-info" style={{ marginTop: 12 }}>
            <span>
              n/a: a company&apos;s figure, such as market cap, P/E, margins, or growth, which an
              ETF does not have, since it holds many companies. Screening on one of them leaves ETFs
              out.
            </span>
            <InfoTip topic="notForFunds" />
          </p>
        )}
        {shown && shown.items.length === 0 && (
          <div className="empty">No stocks match these filters.</div>
        )}
        {!empty && (
          <p className="disclaimer" style={{ marginTop: 12 }}>
            Companies that report in another currency are converted to US dollars at rates worked
            out from the European Central Bank&apos;s euro reference rates (source: European Central
            Bank), or at a market data provider&apos;s rate for currencies the ECB does not publish.
          </p>
        )}
      </section>
    </div>
  )
}
