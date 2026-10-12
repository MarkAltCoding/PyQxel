import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteBacktest, getBacktest, listBacktests, runBacktest } from '../api/endpoints'
import type {
  BacktestRequest,
  BacktestResponse,
  PerformanceMetrics,
  Strategy,
  StrategyType,
} from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { EquityChart, EquityTable } from '../charts/EquityChart'
import { FACTOR_NAMES } from '../lib/factors'
import { useChartTheme } from '../charts/theme'
import { ErrorNotice, Notice } from '../components/Notice'
import { SearchBox } from '../components/SearchBox'
import { Stat } from '../components/Stat'
import { dateTime, number, percent, shortDate, signedPercent, tone } from '../lib/format'

const STRATEGIES: { value: StrategyType; label: string; about: string }[] = [
  {
    value: 'sma_crossover',
    label: 'Moving-average crossover',
    about: 'Long while the fast average is above the slow one.',
  },
  {
    value: 'time_series_momentum',
    label: 'Time-series momentum',
    about: 'Long while the trailing return is positive.',
  },
  {
    value: 'mean_reversion',
    label: 'Mean reversion',
    about: 'Buys stretched drops below the moving average, sells on the way back.',
  },
  { value: 'buy_and_hold', label: 'Buy and hold', about: 'Always invested.' },
]

const STRATEGY_LABEL = Object.fromEntries(STRATEGIES.map((s) => [s.value, s.label]))

interface Form {
  symbol: string
  type: StrategyType
  fast: string
  slow: string
  lookback: string
  skip: string
  window: string
  entryZ: string
  exitZ: string
  allowShort: boolean
  period: NonNullable<BacktestRequest['period']>
  interval: NonNullable<BacktestRequest['interval']>
  costBps: string
  cashInterest: boolean
}

const DEFAULTS: Form = {
  symbol: '',
  type: 'sma_crossover',
  fast: '50',
  slow: '200',
  lookback: '252',
  skip: '21',
  window: '20',
  entryZ: '2',
  exitZ: '0',
  allowShort: false,
  period: '10y',
  interval: '1d',
  costBps: '5',
  cashInterest: true,
}

function strategyOf(form: Form): Strategy {
  switch (form.type) {
    case 'sma_crossover':
      return {
        type: 'sma_crossover',
        fast: Number(form.fast),
        slow: Number(form.slow),
        allow_short: form.allowShort,
      }
    case 'time_series_momentum':
      return {
        type: 'time_series_momentum',
        lookback: Number(form.lookback),
        skip: Number(form.skip),
        allow_short: form.allowShort,
      }
    case 'mean_reversion':
      return {
        type: 'mean_reversion',
        window: Number(form.window),
        entry_z: Number(form.entryZ),
        exit_z: Number(form.exitZ),
        allow_short: form.allowShort,
      }
    default:
      return { type: 'buy_and_hold' }
  }
}

export function BacktestPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const openId = params.get('id')
  const [form, setForm] = useState<Form>(() => ({
    ...DEFAULTS,
    symbol: params.get('symbol')?.toUpperCase() ?? '',
  }))
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const saved = useQuery({ queryKey: ['backtests'], queryFn: listBacktests })
  const opened = useQuery({
    queryKey: ['backtest', openId],
    queryFn: () => getBacktest(openId!),
    enabled: Boolean(openId),
    staleTime: Infinity,
  })
  const run = useMutation({
    mutationFn: () =>
      runBacktest(form.symbol, {
        strategy: strategyOf(form),
        period: form.period,
        interval: form.interval,
        cost_bps: Number(form.costBps),
        cash_interest: form.cashInterest,
      }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      if (result.id) {
        queryClient.setQueryData(['backtest', result.id], result)
        setParams({ id: result.id })
      }
    },
  })
  const remove = useMutation({
    mutationFn: deleteBacktest,
    onSuccess: (_, id) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      if (id === openId) setParams({})
    },
  })

  const result = (openId ? opened.data : undefined) ?? run.data
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (form.symbol) run.mutate()
  }
  const strategy = STRATEGIES.find((s) => s.value === form.type)

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Backtest</h1>
          <div className="subtitle">
            Run a trading rule over history and compare it with buying and holding.
          </div>
        </div>
      </header>

      <form className="card stack" style={{ gap: 14 }} onSubmit={submit}>
        <div className="filter-row">
          <div className="field" style={{ width: 220 }}>
            <span>Symbol</span>
            <SearchBox
              label="Symbol"
              placeholder={form.symbol || 'Ticker or company'}
              onSelect={(symbol) => set('symbol', symbol)}
            />
          </div>
          <label className="field">
            <span>Strategy</span>
            <select
              value={form.type}
              onChange={(event) => set('type', event.target.value as StrategyType)}
            >
              {STRATEGIES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {form.type === 'sma_crossover' && (
            <>
              <NumberField
                label="Fast (bars)"
                value={form.fast}
                onChange={(v) => set('fast', v)}
                min={2}
                max={250}
              />
              <NumberField
                label="Slow (bars)"
                value={form.slow}
                onChange={(v) => set('slow', v)}
                min={3}
                max={500}
              />
            </>
          )}
          {form.type === 'time_series_momentum' && (
            <>
              <NumberField
                label="Lookback (bars)"
                value={form.lookback}
                onChange={(v) => set('lookback', v)}
                min={5}
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
          {form.type === 'mean_reversion' && (
            <>
              <NumberField
                label="Window (bars)"
                value={form.window}
                onChange={(v) => set('window', v)}
                min={5}
                max={250}
              />
              <NumberField
                label="Entry z"
                value={form.entryZ}
                onChange={(v) => set('entryZ', v)}
                min={0.1}
                max={5}
                step={0.1}
              />
              <NumberField
                label="Exit z"
                value={form.exitZ}
                onChange={(v) => set('exitZ', v)}
                min={-5}
                max={5}
                step={0.1}
              />
            </>
          )}
        </div>
        <div className="filter-row">
          <label className="field">
            <span>History</span>
            <select
              value={form.period}
              onChange={(event) => set('period', event.target.value as Form['period'])}
            >
              <option value="1y">1 year</option>
              <option value="2y">2 years</option>
              <option value="5y">5 years</option>
              <option value="10y">10 years</option>
              <option value="max">All</option>
            </select>
          </label>
          <label className="field">
            <span>Bars</span>
            <select
              value={form.interval}
              onChange={(event) => set('interval', event.target.value as Form['interval'])}
            >
              <option value="1d">Daily</option>
              <option value="1wk">Weekly</option>
            </select>
          </label>
          <NumberField
            label="Trading cost (bps)"
            value={form.costBps}
            onChange={(v) => set('costBps', v)}
            min={0}
            max={500}
          />
          {form.type !== 'buy_and_hold' && (
            <label className="checkbox">
              <input
                type="checkbox"
                checked={form.allowShort}
                onChange={(event) => set('allowShort', event.target.checked)}
              />
              Short instead of flat
            </label>
          )}
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
            disabled={!form.symbol || run.isPending}
            style={{ marginLeft: 'auto' }}
          >
            {run.isPending ? 'Running…' : form.symbol ? `Backtest ${form.symbol}` : 'Backtest'}
          </button>
        </div>
        {strategy && <p className="disclaimer">{strategy.about}</p>}
        {run.error !== null && <ErrorNotice error={run.error} />}
      </form>

      {opened.error !== null && <ErrorNotice error={opened.error} />}
      {result && <BacktestResult result={result} refreshing={run.isPending} />}

      <section className="card">
        <div className="card-header">
          <h2>Saved backtests</h2>
        </div>
        {saved.error !== null && <ErrorNotice error={saved.error} />}
        {saved.data && saved.data.items.length === 0 && (
          <div className="empty">Backtests you run are saved here.</div>
        )}
        {saved.data && saved.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>History</th>
                  <th className="num">Return</th>
                  <th className="num">Buy and hold</th>
                  <th className="num">Sharpe</th>
                  <th className="num">Max drawdown</th>
                  <th>Saved</th>
                  <th>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {saved.data.items.map((item) => (
                  <tr
                    key={item.id}
                    className="clickable"
                    aria-current={item.id === openId ? 'true' : undefined}
                    onClick={() => setParams({ id: item.id })}
                  >
                    <td>
                      <Link to={`?id=${item.id}`} onClick={(event) => event.stopPropagation()}>
                        {item.symbol}
                      </Link>
                    </td>
                    <td>{STRATEGY_LABEL[item.strategy] ?? item.strategy}</td>
                    <td>
                      {item.period} · {item.interval === '1d' ? 'daily' : 'weekly'}
                    </td>
                    <td className={`num ${tone(item.total_return) ?? ''}`}>
                      {signedPercent(item.total_return)}
                    </td>
                    <td className="num">{signedPercent(item.benchmark_total_return)}</td>
                    <td className="num">{number(item.sharpe_ratio)}</td>
                    <td className="num">{percent(item.max_drawdown)}</td>
                    <td className="muted">{dateTime(item.saved_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="button ghost small danger"
                        disabled={remove.isPending}
                        onClick={(event) => {
                          event.stopPropagation()
                          remove.mutate(item.id)
                        }}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function NumberField(props: {
  label: string
  value: string
  onChange: (value: string) => void
  min: number
  max: number
  step?: number
}) {
  return (
    <label className="field" style={{ width: 120 }}>
      <span>{props.label}</span>
      <input
        type="number"
        required
        min={props.min}
        max={props.max}
        step={props.step ?? 1}
        value={props.value}
        onChange={(event) => props.onChange(event.target.value)}
      />
    </label>
  )
}

function describe(strategy: BacktestResponse['strategy']): string {
  switch (strategy.type) {
    case 'sma_crossover':
      return `${strategy.fast}/${strategy.slow} moving-average crossover`
    case 'time_series_momentum':
      return `${strategy.lookback}-bar momentum, skipping ${strategy.skip}`
    case 'mean_reversion':
      return `Mean reversion, ${strategy.window}-bar window, entry z ${strategy.entry_z}`
    default:
      return 'Buy and hold'
  }
}

function BacktestResult({ result, refreshing }: { result: BacktestResponse; refreshing: boolean }) {
  const theme = useChartTheme()
  const m = result.metrics
  const b = result.benchmark
  return (
    <>
      <ChartCard
        title={`${result.symbol}: ${describe(result.strategy)}`}
        subtitle={`${shortDate(m.start)} to ${shortDate(m.end)} · ${result.trades} trades · invested ${percent(result.exposure, 0)} of the time · ${result.cost_bps} bps per trade`}
        refreshing={refreshing}
        legend={
          <Legend
            items={[
              { label: 'Strategy', color: theme.series1 },
              { label: 'Buy and hold', color: theme.series2 },
            ]}
          />
        }
        chart={<EquityChart points={result.equity_curve} />}
        table={<EquityTable points={result.equity_curve} />}
        footer={
          <div className="stack" style={{ gap: 12, marginTop: 14 }}>
            <div className="stats">
              <Stat
                label="Total return"
                value={signedPercent(m.total_return)}
                className={tone(m.total_return)}
                detail={`Buy and hold ${signedPercent(b.total_return)}`}
              />
              <Stat
                label="Annualized"
                value={signedPercent(m.annualized_return)}
                detail={`Buy and hold ${signedPercent(b.annualized_return)}`}
              />
              <Stat
                label="Sharpe ratio"
                value={number(m.sharpe_ratio)}
                detail={`Buy and hold ${number(b.sharpe_ratio)}`}
              />
              <Stat
                label="Max drawdown"
                value={percent(m.max_drawdown)}
                detail={`Buy and hold ${percent(b.max_drawdown)}`}
              />
            </div>
            {result.notice && <Notice tone="warning">{result.notice}</Notice>}
            <p className="disclaimer">{result.disclaimer}</p>
          </div>
        }
      />
      <div className="grid-2">
        <section className="card">
          <div className="card-header">
            <h2>Performance</h2>
          </div>
          <MetricsTable strategy={m} benchmark={b} />
        </section>
        <section className="card">
          <div className="card-header">
            <div>
              <h2>Return attribution</h2>
              <div className="subtitle">
                The strategy's daily returns regressed on factor returns
              </div>
            </div>
          </div>
          {result.attribution ? (
            <table className="data">
              <tbody>
                <Row label="Alpha (annualized)">
                  {signedPercent(result.attribution.fit.alpha.estimate)} (t{' '}
                  {number(result.attribution.fit.alpha.t_stat)})
                </Row>
                {result.attribution.fit.exposures.map((exposure) => (
                  <Row key={exposure.factor} label={`${FACTOR_NAMES[exposure.factor]} beta`}>
                    {number(exposure.estimate)}
                  </Row>
                ))}
                <Row label="R²">{percent(result.attribution.fit.r_squared, 0)}</Row>
              </tbody>
            </table>
          ) : (
            <div className="empty">No attribution for this backtest.</div>
          )}
        </section>
      </div>
    </>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <tr>
      <td>{label}</td>
      <td className="num">{children}</td>
    </tr>
  )
}

function MetricsTable({
  strategy,
  benchmark,
}: {
  strategy: PerformanceMetrics
  benchmark: PerformanceMetrics
}) {
  const rows: [string, (m: PerformanceMetrics) => string][] = [
    ['Total return', (m) => signedPercent(m.total_return)],
    ['Annualized return', (m) => signedPercent(m.annualized_return)],
    ['Annualized volatility', (m) => percent(m.annualized_volatility)],
    ['Sharpe ratio', (m) => number(m.sharpe_ratio)],
    ['Sortino ratio', (m) => number(m.sortino_ratio)],
    ['Max drawdown', (m) => percent(m.max_drawdown)],
    ['Drawdown trough', (m) => shortDate(m.max_drawdown_trough)],
    [
      'Recovered',
      (m) => (m.max_drawdown_recovery ? shortDate(m.max_drawdown_recovery) : 'Not yet'),
    ],
  ]
  return (
    <table className="data">
      <thead>
        <tr>
          <th />
          <th className="num">Strategy</th>
          <th className="num">Buy and hold</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([label, format]) => (
          <tr key={label}>
            <td>{label}</td>
            <td className="num">{format(strategy)}</td>
            <td className="num">{format(benchmark)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
