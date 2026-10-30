import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getHoldings, getPortfolios, runCrossSectionalBacktest } from '../api/endpoints'
import type {
  CrossSectionalBacktestRequest,
  CrossSectionalBacktestResponse,
  RankedAsset,
  RankingSignal,
  RankingSignalType,
} from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { EquityChart, EquityTable } from '../charts/EquityChart'
import { useChartTheme } from '../charts/theme'
import { number, percent, shortDate, signedPercent, tone } from '../lib/format'
import { readStored, writeStored } from '../lib/storage'
import { AttributionCard, MetricsTable, NumberField } from './BacktestParts'
import { ErrorNotice, Notice } from './Notice'
import { InfoTip } from './InfoTip'
import { Stat } from './Stat'

/** Most symbols the API ranks at once. */
const MAX_SYMBOLS = 60
const SYMBOL = /^[A-Z0-9.\-^=]{1,15}$/
const UNIVERSE_KEY = 'pyqxel.backtestUniverse'
const BENCHMARK = 'Equal weight'

const SIGNALS: { value: RankingSignalType; label: string; about: string }[] = [
  {
    value: 'momentum',
    label: 'Momentum',
    about:
      'Buys the stocks with the highest return from the lookback to the skip, by default the last year leaving out the latest month.',
  },
  {
    value: 'risk_adjusted_momentum',
    label: 'Risk-adjusted momentum',
    about:
      'Momentum divided by the volatility over the same days: steady winners rank above erratic ones.',
  },
  {
    value: 'trend',
    label: 'Trend strength',
    about: 'Buys the stocks furthest above their moving average.',
  },
  {
    value: 'reversal',
    label: 'Short-term reversal',
    about: 'Buys the recent losers, which tend to bounce back against the recent winners.',
  },
  {
    value: 'low_volatility',
    label: 'Low volatility',
    about: 'Buys the stocks whose daily returns have been calmest.',
  },
]

const SECTOR_ETFS = ['XLB', 'XLC', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLRE', 'XLU', 'XLV', 'XLY']

const PRESETS: { label: string; symbols: string[] }[] = [
  { label: 'Sector ETFs', symbols: SECTOR_ETFS },
  {
    label: 'Mega caps',
    symbols: [
      'AAPL',
      'MSFT',
      'NVDA',
      'AMZN',
      'GOOGL',
      'META',
      'BRK-B',
      'AVGO',
      'TSLA',
      'JPM',
      'LLY',
      'V',
      'UNH',
      'XOM',
      'MA',
      'JNJ',
      'PG',
      'HD',
      'COST',
      'WMT',
    ],
  },
  {
    label: 'Asset classes',
    symbols: ['SPY', 'EFA', 'EEM', 'TLT', 'IEF', 'LQD', 'HYG', 'GLD', 'DBC', 'VNQ'],
  },
]

interface Form {
  symbols: string
  signal: RankingSignalType
  lookback: string
  skip: string
  reversalLookback: string
  window: string
  rebalance: NonNullable<CrossSectionalBacktestRequest['rebalance']>
  legSize: 'quantile' | 'count'
  quantile: string
  topN: string
  longShort: boolean
  weighting: NonNullable<CrossSectionalBacktestRequest['weighting']>
  period: NonNullable<CrossSectionalBacktestRequest['period']>
  costBps: string
  cashInterest: boolean
}

const DEFAULTS: Form = {
  symbols: SECTOR_ETFS.join(' '),
  signal: 'momentum',
  lookback: '252',
  skip: '21',
  reversalLookback: '21',
  window: '200',
  rebalance: 'monthly',
  legSize: 'count',
  quantile: '20',
  topN: '3',
  longShort: false,
  weighting: 'equal',
  period: '10y',
  costBps: '5',
  cashInterest: true,
}

/** Distinct upper-case symbols in `text`, split on spaces, commas and semicolons. */
function parseSymbols(text: string): string[] {
  const symbols = text
    .split(/[\s,;]+/)
    .map((symbol) => symbol.trim().toUpperCase())
    .filter(Boolean)
  return [...new Set(symbols)]
}

function signalOf(form: Form): RankingSignal {
  switch (form.signal) {
    case 'momentum':
    case 'risk_adjusted_momentum':
      return { type: form.signal, lookback: Number(form.lookback), skip: Number(form.skip) }
    case 'reversal':
      return { type: 'reversal', lookback: Number(form.reversalLookback) }
    case 'low_volatility':
    case 'trend':
      return { type: form.signal, window: Number(form.window) }
  }
}

function describe(result: CrossSectionalBacktestResponse): string {
  const { signal } = result
  const name = SIGNALS.find((s) => s.value === signal.type)?.label ?? signal.type
  const window =
    signal.type === 'momentum' || signal.type === 'risk_adjusted_momentum'
      ? `${signal.lookback ?? 252}/${signal.skip ?? 21} bars`
      : signal.type === 'reversal'
        ? `${signal.lookback ?? 21} bars`
        : `${signal.window ?? 0} bars`
  return `${name} (${window}), ${result.long_short ? 'long-short' : 'long-only'}, ${result.rebalance}`
}

/** A stock's score at the last ranking, in the signal's own terms. */
function scoreText(signal: RankingSignal, score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  switch (signal.type) {
    case 'reversal':
      return signedPercent(-score)
    case 'low_volatility':
      return percent(-score * Math.sqrt(252))
    case 'risk_adjusted_momentum':
      return number(score)
    default:
      return signedPercent(score)
  }
}

const SCORE_LABEL: Record<RankingSignalType, string> = {
  momentum: 'Past return',
  risk_adjusted_momentum: 'Return / risk',
  trend: 'Above average',
  reversal: 'Recent return',
  low_volatility: 'Volatility',
}

export function CrossSectionalBacktest() {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<Form>(() => ({
    ...DEFAULTS,
    symbols: readStored(UNIVERSE_KEY) ?? DEFAULTS.symbols,
  }))
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => ({ ...current, [key]: value }))
  const setSymbols = (text: string) => {
    set('symbols', text)
    writeStored(UNIVERSE_KEY, text)
  }
  const symbols = parseSymbols(form.symbols)
  const invalid = symbols.filter((symbol) => !SYMBOL.test(symbol))
  const tooFew = symbols.length < 3
  const tooMany = symbols.length > MAX_SYMBOLS

  const portfolios = useQuery({ queryKey: ['portfolios'], queryFn: getPortfolios })
  const saved = (portfolios.data?.portfolios ?? []).filter((p) => p.holdings > 0)
  const holdings = useMutation({
    mutationFn: (portfolio: number) =>
      queryClient.fetchQuery({
        queryKey: ['holdings', portfolio],
        queryFn: () => getHoldings(portfolio),
      }),
    onSuccess: (data) => setSymbols(data.holdings.map((holding) => holding.symbol).join(' ')),
  })
  const run = useMutation({
    mutationFn: () =>
      runCrossSectionalBacktest({
        symbols,
        signal: signalOf(form),
        rebalance: form.rebalance,
        ...(form.legSize === 'count'
          ? { top_n: Number(form.topN) }
          : { quantile: Number(form.quantile) / 100 }),
        long_short: form.longShort,
        weighting: form.weighting,
        period: form.period,
        cost_bps: Number(form.costBps),
        cash_interest: form.cashInterest,
      }),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!tooFew && !tooMany && invalid.length === 0) run.mutate()
  }
  const signal = SIGNALS.find((s) => s.value === form.signal)
  const maxLeg = form.longShort ? Math.floor(symbols.length / 2) : symbols.length

  return (
    <>
      <form id="ranking-form" className="card stack" style={{ gap: 14 }} onSubmit={submit}>
        <label className="field">
          <span>Universe ({symbols.length} symbols)</span>
          <textarea
            rows={2}
            spellCheck={false}
            placeholder="Tickers separated by spaces or commas, e.g. XLK XLF XLE"
            value={form.symbols}
            onChange={(event) => setSymbols(event.target.value)}
          />
          {invalid.length > 0 && <small>Not ticker symbols: {invalid.join(', ')}</small>}
          {tooMany && <small>At most {MAX_SYMBOLS} symbols can be ranked at once.</small>}
        </label>
        <div className="filter-row">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="button ghost small"
              onClick={() => setSymbols(preset.symbols.join(' '))}
            >
              {preset.label}
            </button>
          ))}
          {saved.map((portfolio) => (
            <button
              key={portfolio.portfolio}
              type="button"
              className="button ghost small"
              disabled={holdings.isPending}
              onClick={() => holdings.mutate(portfolio.portfolio)}
            >
              {saved.length > 1 ? portfolio.name : 'My holdings'}
            </button>
          ))}
        </div>
        {holdings.error !== null && <ErrorNotice error={holdings.error} />}
        <div className="filter-row">
          <label className="field">
            <span>Rank by</span>
            <select
              value={form.signal}
              onChange={(event) => set('signal', event.target.value as RankingSignalType)}
            >
              {SIGNALS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {(form.signal === 'momentum' || form.signal === 'risk_adjusted_momentum') && (
            <>
              <NumberField
                label="Lookback (bars)"
                value={form.lookback}
                onChange={(v) => set('lookback', v)}
                min={20}
                max={756}
              />
              <NumberField
                label="Skip (bars)"
                value={form.skip}
                onChange={(v) => set('skip', v)}
                min={0}
                max={126}
              />
            </>
          )}
          {form.signal === 'reversal' && (
            <NumberField
              label="Lookback (bars)"
              value={form.reversalLookback}
              onChange={(v) => set('reversalLookback', v)}
              min={2}
              max={126}
            />
          )}
          {(form.signal === 'trend' || form.signal === 'low_volatility') && (
            <NumberField
              label="Window (bars)"
              value={form.window}
              onChange={(v) => set('window', v)}
              min={form.signal === 'trend' ? 5 : 10}
              max={form.signal === 'trend' ? 500 : 504}
            />
          )}
          <label className="field">
            <span>Rebalance</span>
            <select
              value={form.rebalance}
              onChange={(event) => set('rebalance', event.target.value as Form['rebalance'])}
            >
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
            </select>
          </label>
          <label className="field">
            <span>Hold</span>
            <select
              value={form.legSize}
              onChange={(event) => set('legSize', event.target.value as Form['legSize'])}
            >
              <option value="count">Top stocks</option>
              <option value="quantile">Top share</option>
            </select>
          </label>
          {form.legSize === 'count' ? (
            <NumberField
              label="Stocks per leg"
              value={form.topN}
              onChange={(v) => set('topN', v)}
              min={1}
              max={Math.max(1, maxLeg)}
            />
          ) : (
            <NumberField
              label="Share per leg (%)"
              value={form.quantile}
              onChange={(v) => set('quantile', v)}
              min={1}
              max={form.longShort ? 50 : 100}
            />
          )}
          <label className="field">
            <span>Weights</span>
            <select
              value={form.weighting}
              onChange={(event) => set('weighting', event.target.value as Form['weighting'])}
            >
              <option value="equal">Equal</option>
              <option value="inverse_volatility">Inverse volatility</option>
            </select>
          </label>
        </div>
        <div className="filter-row">
          <label className="field">
            <span>History</span>
            <select
              value={form.period}
              onChange={(event) => set('period', event.target.value as Form['period'])}
            >
              <option value="2y">2 years</option>
              <option value="5y">5 years</option>
              <option value="10y">10 years</option>
              <option value="max">All</option>
            </select>
          </label>
          <NumberField
            label="Trading cost (bps)"
            value={form.costBps}
            onChange={(v) => set('costBps', v)}
            min={0}
            max={500}
          />
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.longShort}
              onChange={(event) => set('longShort', event.target.checked)}
            />
            Short the bottom ranks too
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={form.cashInterest}
              onChange={(event) => set('cashInterest', event.target.checked)}
            />
            Earn T-bill interest on cash
          </label>
          <button
            type="submit"
            className="button primary"
            disabled={tooFew || tooMany || invalid.length > 0 || run.isPending}
            style={{ marginLeft: 'auto' }}
          >
            {run.isPending ? 'Running…' : `Backtest ${symbols.length} symbols`}
          </button>
        </div>
        {signal && (
          <p className="disclaimer">
            {signal.about}{' '}
            {form.longShort
              ? 'The same number of the lowest-ranked stocks are shorted, so the portfolio is dollar-neutral.'
              : ''}
          </p>
        )}
        {run.error !== null && <ErrorNotice error={run.error} />}
      </form>

      {run.data && <UniverseResult result={run.data} refreshing={run.isPending} />}
    </>
  )
}

function UniverseResult({
  result,
  refreshing,
}: {
  result: CrossSectionalBacktestResponse
  refreshing: boolean
}) {
  const theme = useChartTheme()
  const m = result.metrics
  const b = result.benchmark
  const ic = result.information_coefficient
  const assets = [...result.assets].sort(
    (x, y) =>
      y.weight - x.weight ||
      (y.score ?? -Infinity) - (x.score ?? -Infinity) ||
      x.symbol.localeCompare(y.symbol),
  )
  return (
    <>
      <ChartCard
        id="result"
        info="equityChart"
        title={`${result.symbols.length} symbols: ${describe(result)}`}
        subtitle={`${shortDate(m.start)} to ${shortDate(m.end)} · ${result.rebalances} rebalances · ${number(result.holdings_per_leg, 1)} stocks per leg · ${percent(result.turnover, 0)} traded per rebalance · ${result.cost_bps} bps per trade`}
        refreshing={refreshing}
        legend={
          <Legend
            items={[
              { label: 'Strategy', color: theme.series1 },
              { label: BENCHMARK, color: theme.series2 },
            ]}
          />
        }
        chart={
          <EquityChart
            points={result.equity_curve}
            benchmarkLabel={BENCHMARK}
            benchmarkShort="Equal"
          />
        }
        table={<EquityTable points={result.equity_curve} benchmarkLabel={BENCHMARK} />}
        footer={
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <div className="stats">
              <Stat
                info="totalReturn"
                label="Total return"
                value={signedPercent(m.total_return)}
                className={tone(m.total_return)}
                detail={`${BENCHMARK} ${signedPercent(b.total_return)}`}
              />
              <Stat
                info="annualizedReturn"
                label="Annualized"
                value={signedPercent(m.annualized_return)}
                detail={`${BENCHMARK} ${signedPercent(b.annualized_return)}`}
              />
              <Stat
                info="sharpe"
                label="Sharpe ratio"
                value={number(m.sharpe_ratio)}
                detail={`${BENCHMARK} ${number(b.sharpe_ratio)}`}
              />
              <Stat
                info="maxDrawdown"
                label="Max drawdown"
                value={percent(m.max_drawdown)}
                detail={`${BENCHMARK} ${percent(b.max_drawdown)}`}
              />
              <Stat
                info="rankIc"
                label="Rank IC"
                value={ic ? number(ic.mean) : '—'}
                detail={
                  ic
                    ? `t ${number(ic.t_stat)} · positive ${percent(ic.hit_rate, 0)} of ${ic.observations}`
                    : 'Too few rankings'
                }
              />
            </div>
            {result.notice && <Notice tone="warning">{result.notice}</Notice>}
            <p className="disclaimer">{result.disclaimer}</p>
          </div>
        }
      />
      <div className="grid-2">
        <section id="performance" className="card">
          <div className="card-header">
            <div className="heading-with-info">
              <h2>Performance</h2>
              <InfoTip topic="backtestPerformance" />
            </div>
          </div>
          <MetricsTable strategy={m} benchmark={b} benchmarkLabel={BENCHMARK} />
        </section>
        <AttributionCard attribution={result.attribution} />
      </div>
      <section id="stocks" className="card">
        <div className="card-header">
          <div>
            <h2>Stocks</h2>
            <div className="subtitle">
              The latest ranking, and how often each stock was held over the backtest
            </div>
          </div>
        </div>
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Symbol</th>
                <th className="num">
                  {SCORE_LABEL[result.signal.type]}
                  <InfoTip topic="rankingScore" />
                </th>
                <th className="num">
                  Weight now
                  <InfoTip topic="weightNow" />
                </th>
                <th className="num">
                  Held long
                  <InfoTip topic="heldLong" />
                </th>
                {result.long_short && (
                  <th className="num">
                    Held short
                    <InfoTip topic="heldShort" />
                  </th>
                )}
                <th className="num">
                  Contribution
                  <InfoTip topic="contribution" />
                </th>
                <th>
                  Ranked from
                  <InfoTip topic="rankedFrom" />
                </th>
              </tr>
            </thead>
            <tbody>
              {assets.map((asset) => (
                <AssetRow
                  key={asset.symbol}
                  asset={asset}
                  signal={result.signal}
                  longShort={result.long_short}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  )
}

function AssetRow({
  asset,
  signal,
  longShort,
}: {
  asset: RankedAsset
  signal: RankingSignal
  longShort: boolean
}) {
  return (
    <tr>
      <td>{asset.symbol}</td>
      <td className="num">{scoreText(signal, asset.score)}</td>
      <td className={`num ${tone(asset.weight) ?? ''}`}>
        {asset.weight === 0 ? '—' : signedPercent(asset.weight, 0)}
      </td>
      <td className="num">{percent(asset.long_share, 0)}</td>
      {longShort && <td className="num">{percent(asset.short_share, 0)}</td>}
      <td className={`num ${tone(asset.contribution) ?? ''}`}>
        {signedPercent(asset.contribution)}
      </td>
      <td className="muted">{asset.first_ranked ? shortDate(asset.first_ranked) : 'Never'}</td>
    </tr>
  )
}
