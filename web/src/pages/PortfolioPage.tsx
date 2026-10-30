import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fitCopula, getHoldings, getPortfolios, saveHoldings, simulate } from '../api/endpoints'
import { PortfolioReviewCard } from '../components/PortfolioReviewCard'
import type {
  HoldingsResponse,
  LeftOutHolding,
  SimulationRequest,
  SimulationResponse,
} from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { CopulaHeatmap, CopulaTable } from '../charts/CopulaHeatmap'
import { FanChart, FanTable } from '../charts/FanChart'
import { useChartTheme } from '../charts/theme'
import { ErrorNotice, Notice } from '../components/Notice'
import { SearchBox } from '../components/SearchBox'
import { PresetOrCustom } from '../components/PresetOrCustom'
import { Segmented } from '../components/Segmented'
import { Stat } from '../components/Stat'
import { money, number, percent, signedPercent, tone } from '../lib/format'
import { readStored, writeStored } from '../lib/storage'
import { InfoTip } from '../components/InfoTip'

const MAX_HOLDINGS = 40

/** Portfolios an account may save, as the API allows. */
const PORTFOLIOS = ['1', '2'] as const
type PortfolioNumber = (typeof PORTFOLIOS)[number]

const PORTFOLIO_KEY = 'pyqxel.portfolio'

/** The portfolio this viewer had open last, or the first. */
function storedPortfolio(): PortfolioNumber {
  const stored = readStored(PORTFOLIO_KEY)
  return PORTFOLIOS.find((value) => value === stored) ?? '1'
}

const MAX_NAME_LENGTH = 40

/** How much of the others' history a late listing may cost, in percent; KEEP_ALL keeps
 * every holding with enough history of its own. */
const KEEP_ALL = 100
const HISTORY_CUTS = [
  { value: 25, label: '25%' },
  { value: 50, label: '50%' },
  { value: 75, label: '75%' },
  { value: KEEP_ALL, label: 'Keep all' },
] as const
const HISTORY_CUT_KEY = 'pyqxel.historyCut'

/** The cutoff this viewer chose before, or half. */
function storedHistoryCut(): number {
  const stored = Number(readStored(HISTORY_CUT_KEY))
  return Number.isInteger(stored) && stored >= 1 && stored <= KEEP_ALL ? stored : 50
}

/** The cutoff as the API takes it: a fraction, or null to keep all. */
const asFraction = (percentCut: number): number | null =>
  percentCut >= KEEP_ALL ? null : percentCut / 100

interface Row {
  symbol: string
  shares: string
}

/** Unsaved edits to one portfolio. */
interface Draft {
  rows: Row[]
  name: string
}

const HORIZONS = [
  { value: 21, label: '1M' },
  { value: 63, label: '3M' },
  { value: 126, label: '6M' },
  { value: 252, label: '1Y' },
] as const

/** Longest horizon a simulation may run, in trading days, as the API allows. */
const MAX_HORIZON_DAYS = 1260

/** Paths simulated when the run is small enough, and the API's limits on a run's size:
 * paths x days, and paths x days x holdings. */
const DEFAULT_PATHS = 10_000
const MAX_PATH_DAYS = 2_600_000
const MAX_DRAWS = 15_000_000

/** The most paths, in hundreds and up to the default, a run of this size may have. */
function pathsFor(horizon: number, holdings: number): number {
  const fits = Math.min(MAX_PATH_DAYS / horizon, MAX_DRAWS / (horizon * Math.max(1, holdings)))
  return Math.max(100, Math.min(DEFAULT_PATHS, Math.floor(fits / 100) * 100))
}

const DEPENDENCE = [
  { value: 'student_t', label: 'Student t' },
  { value: 'gaussian', label: 'Gaussian' },
  { value: 'empirical', label: 'Historical' },
] as const

const COPULA_PERIODS = [
  { value: '1y', label: '1Y' },
  { value: '2y', label: '2Y' },
  { value: '5y', label: '5Y' },
] as const

const toRows = (data: HoldingsResponse | undefined): Row[] =>
  (data?.holdings ?? []).map((holding) => ({
    symbol: holding.symbol,
    shares: String(holding.shares),
  }))

export function PortfolioPage() {
  const queryClient = useQueryClient()
  const [selected, setSelected] = useState<PortfolioNumber>(storedPortfolio)
  const [historyCut, setHistoryCut] = useState(storedHistoryCut)
  const portfolio = Number(selected)
  const portfolios = useQuery({ queryKey: ['portfolios'], queryFn: getPortfolios })
  const holdings = useQuery({
    queryKey: ['holdings', portfolio],
    queryFn: () => getHoldings(portfolio),
    placeholderData: keepPreviousData,
  })
  const loaded = holdings.isPlaceholderData ? undefined : holdings.data
  // Unsaved edits of each portfolio, kept while switching between them.
  const [drafts, setDrafts] = useState<Partial<Record<PortfolioNumber, Draft>>>({})
  const draft = drafts[selected]
  const dirty = draft !== undefined
  const savedName = loaded?.name ?? `Portfolio ${portfolio}`
  const rows = draft?.rows ?? toRows(loaded)
  const name = draft?.name ?? savedName

  const save = useMutation({
    mutationFn: () =>
      saveHoldings(
        portfolio,
        rows.map((row) => ({ symbol: row.symbol, shares: Number(row.shares) })),
        name.trim() && name.trim() !== savedName ? name.trim() : undefined,
      ),
    onSuccess: (data) => {
      queryClient.setQueryData(['holdings', portfolio], data)
      void queryClient.invalidateQueries({ queryKey: ['portfolios'] })
      discard()
    },
  })

  const edit = (next: Partial<Draft>) =>
    setDrafts((current) => ({ ...current, [selected]: { rows, name, ...next } }))
  const discard = () =>
    setDrafts((current) => {
      const next = { ...current }
      delete next[selected]
      return next
    })
  const invalid = rows.some((row) => !(Number(row.shares) > 0)) || !name.trim()
  const valued = new Map((loaded?.holdings ?? []).map((h) => [h.symbol, h]))

  const priced = (loaded?.holdings ?? []).filter(
    (holding) => holding.weight !== null && holding.weight !== undefined && holding.weight > 0,
  )
  const options = PORTFOLIOS.map((value) => {
    const summary = portfolios.data?.portfolios.find((p) => String(p.portfolio) === value)
    const unsaved = drafts[value] !== undefined ? ' •' : ''
    return { value, label: `${summary?.name ?? `Portfolio ${value}`}${unsaved}` }
  })

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Portfolio</h1>
          <div className="subtitle">
            Your holdings, how they could fare, and how they move together. Save up to two
            portfolios.
          </div>
        </div>
        <Segmented
          label="Portfolio"
          value={selected}
          options={options}
          onChange={(next) => {
            writeStored(PORTFOLIO_KEY, next)
            save.reset()
            setSelected(next)
          }}
        />
      </header>

      <section className="card" aria-busy={holdings.isPlaceholderData}>
        <div className="card-header">
          <div>
            <h2>{savedName}</h2>
            <div className="subtitle">
              Valued at the latest prices · {money(loaded?.total_value)} in total
            </div>
          </div>
          <div className="filter-row">
            <input
              type="text"
              aria-label="Portfolio name"
              maxLength={MAX_NAME_LENGTH}
              style={{ width: 180 }}
              value={name}
              aria-invalid={!name.trim()}
              onChange={(event) => edit({ name: event.target.value })}
            />
            {dirty && (
              <button type="button" className="button ghost" onClick={discard}>
                Discard
              </button>
            )}
            <button
              type="button"
              className="button primary"
              disabled={!dirty || invalid || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
        {holdings.error !== null && <ErrorNotice error={holdings.error} />}
        {save.error !== null && <ErrorNotice error={save.error} />}
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Name</th>
                <th className="num">Shares</th>
                <th className="num">Price</th>
                <th className="num">Value</th>
                <th className="num" style={{ minWidth: 150 }}>
                  Weight
                  <InfoTip topic="holdingWeight" />
                </th>
                <th>
                  <span className="visually-hidden">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const known = valued.get(row.symbol)
                const value = dirty ? undefined : known
                return (
                  <tr key={row.symbol}>
                    <td>
                      <Link to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.symbol}</Link>
                    </td>
                    <td className="muted">
                      {known?.name ?? ''}
                      {known?.cash && (
                        <span className="heading-with-info" style={{ marginLeft: 6 }}>
                          <span className="badge">Cash</span>
                          <InfoTip topic="moneyMarketCash" />
                        </span>
                      )}
                    </td>
                    <td className="num">
                      <input
                        type="number"
                        min="0"
                        step="any"
                        aria-label={`Shares of ${row.symbol}`}
                        style={{ width: 110, textAlign: 'right' }}
                        value={row.shares}
                        aria-invalid={!(Number(row.shares) > 0)}
                        onChange={(event) =>
                          edit({
                            rows: rows.map((r, i) =>
                              i === index ? { ...r, shares: event.target.value } : r,
                            ),
                          })
                        }
                      />
                    </td>
                    <td className="num">{money(known?.price)}</td>
                    <td className="num">{money(value?.value)}</td>
                    <td className="num">
                      <WeightBar weight={value?.weight} />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="button ghost small danger"
                        onClick={() => edit({ rows: rows.filter((_, i) => i !== index) })}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && !holdings.isLoading && (
          <div className="empty">No holdings yet. Add your first one below.</div>
        )}
        <div className="filter-row" style={{ marginTop: 14 }}>
          <div style={{ flex: 1, maxWidth: 360 }}>
            <SearchBox
              clearOnSelect
              label="Add a holding"
              placeholder={
                rows.length >= MAX_HOLDINGS ? `At most ${MAX_HOLDINGS} holdings` : 'Add a holding'
              }
              onSelect={(symbol) => {
                if (rows.length >= MAX_HOLDINGS || rows.some((row) => row.symbol === symbol)) {
                  return
                }
                edit({ rows: [...rows, { symbol, shares: '' }] })
              }}
            />
          </div>
          <span className="muted">
            {rows.length} of {MAX_HOLDINGS} holdings
            {dirty ? ' · Enter share counts, then save.' : ''}
          </span>
        </div>
        {loaded?.notice && !dirty && (
          <div style={{ marginTop: 12 }}>
            <Notice tone="warning">{loaded.notice}</Notice>
          </div>
        )}
      </section>

      <section className="card">
        <div className="card-header" style={{ marginBottom: 0 }}>
          <div>
            <div className="heading-with-info">
              <h2>Late listings</h2>
              <InfoTip topic="historyCut" />
            </div>
            <div className="subtitle">
              The simulation and co-movement models start when the newest holding started trading.
              Leave a holding out when it would cut more than this off the others’ history.
            </div>
          </div>
          <PresetOrCustom
            label="Most history a late listing may cost"
            presets={HISTORY_CUTS}
            value={historyCut}
            onChange={(next) => {
              writeStored(HISTORY_CUT_KEY, String(next))
              setHistoryCut(next)
            }}
            min={1}
            max={99}
            unit="% of history"
          />
        </div>
      </section>

      <SimulationCard
        key={`simulation-${portfolio}`}
        maxHistoryCut={asFraction(historyCut)}
        holdings={priced.map((h) => ({ symbol: h.symbol, weight: h.weight! }))}
        initialValue={loaded?.total_value ?? 0}
        stale={dirty || holdings.isPlaceholderData}
      />
      <CopulaCard
        symbols={priced.map((h) => h.symbol)}
        maxHistoryCut={asFraction(historyCut)}
        stale={dirty || holdings.isPlaceholderData}
      />
      <PortfolioReviewCard
        key={`review-${portfolio}`}
        portfolio={portfolio}
        hasHoldings={priced.length > 0}
        stale={dirty || holdings.isPlaceholderData}
      />
    </div>
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
          width: 80,
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
      <span style={{ minWidth: 48, textAlign: 'right' }}>{percent(weight)}</span>
    </span>
  )
}

/** "A, B and C". */
function listed(items: string[]): string {
  return items.length < 2
    ? (items[0] ?? '')
    : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`
}

/** Says which holdings an analysis could not use, and why, so its figures are read as
 * covering only the rest. */
function LeftOutNotice({
  leftOut,
  share,
  rest,
}: {
  leftOut: LeftOutHolding[]
  /** Share of the portfolio's value they hold, when known. */
  share?: number
  /** What became of the holdings kept. */
  rest: string
}) {
  if (leftOut.length === 0) return null
  return (
    <Notice tone="warning">
      <div className="heading-with-info">
        <strong>
          Left out: {listed(leftOut.map((h) => h.symbol))}
          {share !== undefined && ` (${percent(share, 0)} of the portfolio)`}
        </strong>
        <InfoTip topic="leftOutHoldings" />
      </div>
      <ul style={{ margin: '4px 0', paddingLeft: 18 }}>
        {leftOut.map((holding) => (
          <li key={holding.symbol}>
            {holding.symbol}: {holding.detail}
          </li>
        ))}
      </ul>
      {rest}
    </Notice>
  )
}

/** Normalizes rounding so weights sum to exactly one, as the API requires. */
function normalized(holdings: { symbol: string; weight: number }[]) {
  const total = holdings.reduce((sum, h) => sum + h.weight, 0)
  return holdings.map((h) => ({ symbol: h.symbol, weight: h.weight / total }))
}

function SimulationCard({
  holdings,
  initialValue,
  maxHistoryCut,
  stale,
}: {
  holdings: { symbol: string; weight: number }[]
  initialValue: number
  /** Most of the others' history a late listing may cost, as a fraction; null keeps all. */
  maxHistoryCut: number | null
  stale: boolean
}) {
  const theme = useChartTheme()
  const [horizon, setHorizon] = useState(63)
  const [dependence, setDependence] = useState<(typeof DEPENDENCE)[number]['value']>('student_t')
  const run = useMutation({
    mutationFn: (request: SimulationRequest) => simulate(request),
  })
  const result: SimulationResponse | undefined = run.data
  const summary = result?.simulation
  const risk95 = summary?.risk.find((r) => Math.abs(r.confidence - 0.95) < 1e-9)
  const drawdown = summary?.max_drawdown.percentiles.find((p) => p.percentile === 50)

  const start = initialValue > 0 ? initialValue : 10_000
  const disabled = holdings.length === 0 || stale || run.isPending
  const weightOf = new Map(result?.holdings.map((h) => [h.symbol, h.weight]))
  const leftOutShare = (result?.left_out ?? []).reduce(
    (sum, h) => sum + (weightOf.get(h.symbol) ?? 0),
    0,
  )
  const cash = result?.cash ?? []

  return (
    <ChartCard
      info="monteCarlo"
      title="Simulated outcomes"
      subtitle={
        summary
          ? `${summary.paths.toLocaleString()} paths over ${summary.horizon} trading days, fitted on ${result.period} of history`
          : 'Monte Carlo paths from the holdings’ historical returns and how they co-move'
      }
      actions={
        <>
          <PresetOrCustom
            label="Horizon"
            presets={HORIZONS}
            value={horizon}
            onChange={setHorizon}
            min={1}
            max={MAX_HORIZON_DAYS}
            unit="trading days"
          />
          <Segmented
            label="Dependence"
            value={dependence}
            options={DEPENDENCE}
            onChange={setDependence}
          />
          <button
            type="button"
            className="button primary"
            disabled={disabled}
            onClick={() =>
              run.mutate({
                holdings: normalized(holdings),
                horizon,
                dependence,
                paths: pathsFor(horizon, holdings.length),
                initial_value: start,
                period: '5y',
                max_history_cut: maxHistoryCut,
              })
            }
          >
            {run.isPending ? 'Simulating…' : summary ? 'Run again' : 'Run simulation'}
          </button>
        </>
      }
      loading={run.isPending && !summary}
      refreshing={run.isPending}
      error={run.error ?? undefined}
      empty={
        holdings.length === 0
          ? 'Save holdings with prices to simulate them.'
          : stale
            ? 'Save your changes to simulate the new holdings.'
            : 'Run a simulation to see the range of outcomes.'
      }
      legend={
        <Legend
          items={[
            { label: 'Median', color: theme.seq600 },
            { label: 'Middle 50%', color: theme.seq200, shape: 'box' },
            { label: '90% range', color: theme.seq100, shape: 'box' },
          ]}
        />
      }
      chart={summary && <FanChart fan={summary.fan_chart} initial={summary.initial_value} />}
      table={summary && <FanTable fan={summary.fan_chart} />}
      footer={
        summary && (
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <div className="stats">
              <Stat
                info="expectedReturn"
                label="Expected return"
                value={signedPercent(summary.expected_return)}
                className={tone(summary.expected_return)}
              />
              <Stat
                info="medianReturn"
                label="Median return"
                value={signedPercent(summary.median_return)}
              />
              <Stat
                info="chanceOfLoss"
                label="Chance of a loss"
                value={percent(summary.probability_of_loss, 0)}
              />
              <Stat
                info="valueAtRisk"
                label="95% Value at Risk"
                value={money(risk95?.value_at_risk_amount, 'USD', 0)}
                detail={`${percent(risk95?.value_at_risk)} of value`}
              />
              <Stat
                info="expectedShortfall"
                label="95% Expected shortfall"
                value={money(risk95?.conditional_value_at_risk_amount, 'USD', 0)}
                detail="Average loss in the worst 5%"
              />
              <Stat
                info="medianMaxDrawdown"
                label="Median max drawdown"
                value={percent(drawdown?.value)}
                detail={
                  summary.copula_degrees_of_freedom
                    ? `Copula t, ${number(summary.copula_degrees_of_freedom, 1)} d.f.`
                    : undefined
                }
              />
            </div>
            <LeftOutNotice
              leftOut={result.left_out ?? []}
              share={leftOutShare}
              rest="The simulation covers the other holdings, scaled up in proportion to make up the whole portfolio."
            />
            {cash.length > 0 && (
              <Notice>
                <span className="heading-with-info">
                  <span>
                    {listed(cash)}{' '}
                    {cash.length > 1 ? 'are money-market funds' : 'is a money-market fund'},
                    simulated as cash: {cash.length > 1 ? 'they keep their' : 'it keeps its'} value,
                    with no gains, losses or interest.
                  </span>
                  <InfoTip topic="moneyMarketCash" />
                </span>
              </Notice>
            )}
            {(summary.warnings ?? []).map((warning) => (
              <Notice key={warning} tone="warning">
                {warning}
              </Notice>
            ))}
            {result.notice && <Notice tone="warning">{result.notice}</Notice>}
            <p className="disclaimer">{result.disclaimer}</p>
          </div>
        )
      }
    />
  )
}

function CopulaCard({
  symbols,
  maxHistoryCut,
  stale,
}: {
  symbols: string[]
  /** Most of the others' history a late listing may cost, as a fraction; null keeps all. */
  maxHistoryCut: number | null
  stale: boolean
}) {
  const [period, setPeriod] = useState<(typeof COPULA_PERIODS)[number]['value']>('5y')
  const key = useMemo(() => [...symbols].sort().join(','), [symbols])
  const fit = useQuery({
    queryKey: ['copula', key, period, maxHistoryCut],
    queryFn: () => fitCopula(symbols, period, maxHistoryCut),
    enabled: symbols.length >= 2 && !stale,
    placeholderData: keepPreviousData,
    staleTime: 30 * 60_000,
  })
  const data = fit.data
  return (
    <ChartCard
      info="copula"
      title="How the holdings move together"
      subtitle={
        data
          ? `Kendall's tau on ${data.observations.toLocaleString()} common trading days · best fit: ${data.preferred === 'student_t' ? `Student t copula, ${number(data.student_t.degrees_of_freedom, 1)} d.f.` : 'Gaussian copula'}`
          : "Kendall's rank correlation between each pair of holdings"
      }
      actions={
        <Segmented label="History" value={period} options={COPULA_PERIODS} onChange={setPeriod} />
      }
      loading={fit.isLoading}
      refreshing={fit.isPlaceholderData}
      error={fit.error ?? undefined}
      empty={
        symbols.length < 2
          ? 'Save at least two priced holdings to see how they move together.'
          : 'Save your changes to update.'
      }
      chart={data && <CopulaHeatmap fit={data} />}
      table={data && <CopulaTable fit={data} />}
      footer={
        data && (
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <p className="disclaimer">
              Tail dependence is the chance two holdings crash together, beyond what their
              correlation implies; the Student t copula captures it and the Gaussian does not.
            </p>
            <LeftOutNotice
              leftOut={data.left_out ?? []}
              rest={`The matrix compares the other ${data.symbols.length} holdings.`}
            />
            {[...(data.warnings ?? []), ...(data.notice ? [data.notice] : [])].map((warning) => (
              <Notice key={warning} tone="warning">
                {warning}
              </Notice>
            ))}
          </div>
        )
      }
    />
  )
}
