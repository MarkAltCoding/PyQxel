import { lazy, Suspense, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import {
  deleteBacktest,
  getBacktest,
  getHistory,
  listBacktests,
  runBacktest,
  runCustomBacktest,
} from '../api/endpoints'
import type {
  BacktestRequest,
  BacktestResponse,
  CodeLanguage,
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
import { Segmented } from '../components/Segmented'
import { Stat } from '../components/Stat'
import { StrategyRunOutput } from '../components/StrategyRunOutput'
import type { EditorError } from '../editor/StrategyEditor'
import type { EditorLanguage } from '../editor/languages'
import { starterCode } from '../editor/starters'
import { dateTime, number, percent, shortDate, signedPercent, tone } from '../lib/format'
import { readStored, writeStored } from '../lib/storage'
import { runnerFor, StrategyRunError } from '../runners'

// CodeMirror loads only when someone opens a custom strategy.
const StrategyEditor = lazy(() =>
  import('../editor/StrategyEditor').then((module) => ({ default: module.StrategyEditor })),
)

type StrategyGroup = 'Trend' | 'Mean reversion' | 'Risk' | 'Baseline'

const STRATEGIES: { value: StrategyType; label: string; group: StrategyGroup; about: string }[] = [
  {
    value: 'sma_crossover',
    label: 'Moving-average crossover',
    group: 'Trend',
    about: 'Long while the fast average is above the slow one.',
  },
  {
    value: 'macd',
    label: 'MACD',
    group: 'Trend',
    about: 'Long while the MACD line is above its signal line.',
  },
  {
    value: 'donchian_breakout',
    label: 'Donchian breakout',
    group: 'Trend',
    about:
      'Buys a close above the highest close of the entry window, sells one below the lowest close of the exit window.',
  },
  {
    value: 'time_series_momentum',
    label: 'Time-series momentum',
    group: 'Trend',
    about: 'Long while the trailing return is positive.',
  },
  {
    value: 'dual_momentum',
    label: 'Dual momentum',
    group: 'Trend',
    about:
      'Long while the trailing return is positive and at least the benchmark’s over the same window; otherwise flat.',
  },
  {
    value: 'mean_reversion',
    label: 'Mean reversion',
    group: 'Mean reversion',
    about: 'Buys stretched drops below the moving average, sells on the way back.',
  },
  {
    value: 'rsi',
    label: 'RSI',
    group: 'Mean reversion',
    about: 'Buys when the relative strength index is oversold, sells once it is overbought.',
  },
  {
    value: 'bollinger_bands',
    label: 'Bollinger bands',
    group: 'Mean reversion',
    about:
      'Reversion buys at the lower band and sells at the middle; breakout buys through the upper band and sells back at the middle.',
  },
  {
    value: 'volatility_targeting',
    label: 'Volatility targeting',
    group: 'Risk',
    about:
      'Always long, sized so recent volatility matches the target: smaller in turbulent markets, larger in calm ones.',
  },
  { value: 'buy_and_hold', label: 'Buy and hold', group: 'Baseline', about: 'Always invested.' },
]

const GROUPS: StrategyGroup[] = ['Trend', 'Mean reversion', 'Risk', 'Baseline']

const STRATEGY_LABEL: Record<string, string> = {
  ...Object.fromEntries(STRATEGIES.map((s) => [s.value, s.label])),
  custom: 'Custom',
}

/** A built-in strategy, or the user's own code. */
type StrategyChoice = StrategyType | 'custom'

const LANGUAGE_LABEL: Record<CodeLanguage, string> = { python: 'Python', cpp: 'C++' }

const CUSTOM_ABOUT =
  'Your own Python or C++, run in this browser. Only the positions it returns are sent to PyQxel, which backtests them like any built-in strategy.'

/** What each language offers, and what a run says while it loads and runs. */
const LANGUAGE_DETAILS: Record<
  EditorLanguage,
  { subtitle: string; loading: string; running: string }
> = {
  python: {
    subtitle: 'Python 3.14 with NumPy and pandas, run in this browser',
    loading: 'Loading Python…',
    running: 'Running strategy…',
  },
  cpp: {
    subtitle: 'C++20 with its standard library, compiled and run in this browser',
    loading: 'Loading the C++ compiler…',
    running: 'Compiling and running…',
  },
}

const LANGUAGE_OPTIONS: { value: EditorLanguage; label: string }[] = [
  { value: 'python', label: 'Python' },
  { value: 'cpp', label: 'C++' },
]

/** Where the code being written is kept between visits. */
const draftKey = (language: EditorLanguage) => `pyqxel.strategyDraft.${language}`

/** Strategies that are long or flat only, so the short option does not apply. */
const LONG_ONLY = new Set<StrategyType>(['buy_and_hold', 'dual_momentum', 'volatility_targeting'])

interface Form {
  symbol: string
  type: StrategyChoice
  code: string
  language: EditorLanguage
  fast: string
  slow: string
  lookback: string
  skip: string
  window: string
  entryZ: string
  exitZ: string
  rsiPeriod: string
  oversold: string
  overbought: string
  numStd: string
  bandMode: 'reversion' | 'breakout'
  channelEntry: string
  channelExit: string
  benchmark: string
  targetVolatility: string
  maxLeverage: string
  rebalance: string
  macdFast: string
  macdSlow: string
  macdSignal: string
  allowShort: boolean
  period: NonNullable<BacktestRequest['period']>
  interval: NonNullable<BacktestRequest['interval']>
  costBps: string
  cashInterest: boolean
}

const DEFAULTS: Form = {
  symbol: '',
  type: 'sma_crossover',
  code: '',
  language: 'python',
  fast: '50',
  slow: '200',
  lookback: '252',
  skip: '21',
  window: '20',
  entryZ: '2',
  exitZ: '0',
  rsiPeriod: '14',
  oversold: '30',
  overbought: '70',
  numStd: '2',
  bandMode: 'reversion',
  channelEntry: '20',
  channelExit: '10',
  benchmark: 'SPY',
  targetVolatility: '15',
  maxLeverage: '1',
  rebalance: '10',
  macdFast: '12',
  macdSlow: '26',
  macdSignal: '9',
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
    case 'rsi':
      return {
        type: 'rsi',
        period: Number(form.rsiPeriod),
        oversold: Number(form.oversold),
        overbought: Number(form.overbought),
        allow_short: form.allowShort,
      }
    case 'bollinger_bands':
      return {
        type: 'bollinger_bands',
        window: Number(form.window),
        num_std: Number(form.numStd),
        mode: form.bandMode,
        allow_short: form.allowShort,
      }
    case 'donchian_breakout':
      return {
        type: 'donchian_breakout',
        entry: Number(form.channelEntry),
        exit: Number(form.channelExit),
        allow_short: form.allowShort,
      }
    case 'dual_momentum':
      return {
        type: 'dual_momentum',
        lookback: Number(form.lookback),
        skip: Number(form.skip),
        benchmark: form.benchmark.trim().toUpperCase(),
      }
    case 'volatility_targeting':
      return {
        type: 'volatility_targeting',
        target_volatility: Number(form.targetVolatility) / 100,
        window: Number(form.window),
        max_leverage: Number(form.maxLeverage),
        rebalance_threshold: Number(form.rebalance) / 100,
      }
    case 'macd':
      return {
        type: 'macd',
        fast: Number(form.macdFast),
        slow: Number(form.macdSlow),
        signal: Number(form.macdSignal),
        allow_short: form.allowShort,
      }
    case 'custom':
      throw new Error('Custom strategies run in the browser, not as a built-in strategy.')
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
    code: readStored(draftKey(DEFAULTS.language)) ?? starterCode(DEFAULTS.language),
  }))
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => ({ ...current, [key]: value }))
  const custom = form.type === 'custom'
  const setCode = (code: string) => {
    set('code', code)
    writeStored(draftKey(form.language), code)
  }
  /** Switches the custom strategy's language, to that language's own draft or starter. */
  const chooseLanguage = (language: EditorLanguage) => {
    if (language === form.language) return
    setForm((current) => ({
      ...current,
      language,
      code: readStored(draftKey(language)) ?? starterCode(language),
    }))
    // Its runtime starts downloading while the code is read and written.
    runnerFor(language).warm()
  }
  /** What a custom run is doing, for the Run button. */
  const [phase, setPhase] = useState('')
  /** What the last custom run printed. */
  const [printed, setPrinted] = useState('')
  const stopper = useRef<AbortController | null>(null)
  /** The stored backtest whose settings are in the form, so they are loaded only once. */
  const [loadedId, setLoadedId] = useState<string | null>(null)

  const saved = useQuery({ queryKey: ['backtests'], queryFn: listBacktests })
  const opened = useQuery({
    queryKey: ['backtest', openId],
    queryFn: () => getBacktest(openId!),
    enabled: Boolean(openId),
    staleTime: Infinity,
  })
  const run = useMutation({
    mutationFn: async (): Promise<BacktestResponse> => {
      const options = {
        period: form.period,
        interval: form.interval,
        cost_bps: Number(form.costBps),
        cash_interest: form.cashInterest,
      }
      if (form.type !== 'custom') {
        return runBacktest(form.symbol, { strategy: strategyOf(form), ...options })
      }
      // The code runs here, in a worker; the server only sees and scores its positions.
      const controller = new AbortController()
      stopper.current = controller
      try {
        setPrinted('')
        setPhase('Fetching prices…')
        const history = await getHistory(form.symbol, form.period, form.interval)
        if (history.bars.length === 0) {
          throw new ApiError(422, history.notice ?? `${form.symbol} has no prices in this window.`)
        }
        const details = LANGUAGE_DETAILS[form.language]
        setPhase(details.loading)
        const outcome = await runnerFor(form.language).run(form.code, history.bars, {
          signal: controller.signal,
          onStart: () => setPhase(details.running),
        })
        setPrinted(outcome.output)
        if (!outcome.ok) throw new StrategyRunError(outcome.error, outcome.output)
        setPhase('Scoring…')
        return await runCustomBacktest(form.symbol, {
          ...options,
          code: form.code,
          language: form.language,
          timestamps: outcome.timestamps,
          positions: outcome.positions,
        })
      } finally {
        stopper.current = null
      }
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      if (result.id) {
        setLoadedId(result.id)
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

  // Opening a stored custom backtest puts its code and settings back in the form, so it
  // can be rerun as it was or edited. This adjusts state while rendering, once per opened
  // backtest, as React recommends for state that follows other data.
  const openedResult = opened.data
  if (openedResult?.id && openedResult.id !== loadedId) {
    setLoadedId(openedResult.id)
    const { strategy } = openedResult
    if (strategy.type === 'custom') {
      setForm((current) => ({
        ...current,
        type: 'custom',
        language: strategy.language ?? 'python',
        code: strategy.code,
        symbol: openedResult.symbol,
        period: openedResult.period,
        interval: openedResult.interval,
        costBps: String(openedResult.cost_bps),
        cashInterest: openedResult.cash_interest ?? current.cashInterest,
      }))
    }
  }

  const result = (openId ? opened.data : undefined) ?? run.data
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (form.symbol) run.mutate()
  }
  const strategy = STRATEGIES.find((s) => s.value === form.type)
  const about = custom ? CUSTOM_ABOUT : strategy?.about
  // Failures in the browser are shown under the code; the rest, under the form.
  const runFailure = run.error instanceof StrategyRunError ? run.error.failure : null
  const editorError = useMemo<EditorError | null>(
    () =>
      runFailure && runFailure.kind !== 'cancelled'
        ? { line: runFailure.line, message: runFailure.message }
        : null,
    [runFailure],
  )
  const resetCode = () => {
    if (window.confirm('Replace your code with the starting template?')) {
      setCode(starterCode(form.language))
    }
  }

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

      <form id="backtest-form" className="card stack" style={{ gap: 14 }} onSubmit={submit}>
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
              onChange={(event) => {
                const choice = event.target.value as StrategyChoice
                set('type', choice)
                // Python starts downloading while the code is being written.
                if (choice === 'custom') runnerFor(form.language).warm()
              }}
            >
              {GROUPS.map((group) => (
                <optgroup key={group} label={group}>
                  {STRATEGIES.filter((s) => s.group === group).map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </optgroup>
              ))}
              <optgroup label="Custom">
                <option value="custom">Custom code (Python or C++)</option>
              </optgroup>
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
          {(form.type === 'time_series_momentum' || form.type === 'dual_momentum') && (
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
                min={0}
                max={5}
                step={0.1}
              />
            </>
          )}
          {form.type === 'dual_momentum' && (
            <label className="field" style={{ width: 120 }}>
              <span>Benchmark</span>
              <input
                type="text"
                required
                pattern="[A-Za-z0-9.\-^=]{1,15}"
                value={form.benchmark}
                onChange={(event) => set('benchmark', event.target.value.toUpperCase())}
              />
            </label>
          )}
          {form.type === 'rsi' && (
            <>
              <NumberField
                label="RSI period (bars)"
                value={form.rsiPeriod}
                onChange={(v) => set('rsiPeriod', v)}
                min={2}
                max={100}
              />
              <NumberField
                label="Oversold"
                value={form.oversold}
                onChange={(v) => set('oversold', v)}
                min={1}
                max={49}
              />
              <NumberField
                label="Overbought"
                value={form.overbought}
                onChange={(v) => set('overbought', v)}
                min={51}
                max={99}
              />
            </>
          )}
          {form.type === 'bollinger_bands' && (
            <>
              <label className="field">
                <span>Mode</span>
                <select
                  value={form.bandMode}
                  onChange={(event) => set('bandMode', event.target.value as Form['bandMode'])}
                >
                  <option value="reversion">Reversion</option>
                  <option value="breakout">Breakout</option>
                </select>
              </label>
              <NumberField
                label="Window (bars)"
                value={form.window}
                onChange={(v) => set('window', v)}
                min={5}
                max={250}
              />
              <NumberField
                label="Band width (σ)"
                value={form.numStd}
                onChange={(v) => set('numStd', v)}
                min={0.1}
                max={5}
                step={0.1}
              />
            </>
          )}
          {form.type === 'donchian_breakout' && (
            <>
              <NumberField
                label="Entry (bars)"
                value={form.channelEntry}
                onChange={(v) => set('channelEntry', v)}
                min={5}
                max={250}
              />
              <NumberField
                label="Exit (bars)"
                value={form.channelExit}
                onChange={(v) => set('channelExit', v)}
                min={2}
                max={250}
              />
            </>
          )}
          {form.type === 'volatility_targeting' && (
            <>
              <NumberField
                label="Target vol (%)"
                value={form.targetVolatility}
                onChange={(v) => set('targetVolatility', v)}
                min={1}
                max={100}
              />
              <NumberField
                label="Window (bars)"
                value={form.window}
                onChange={(v) => set('window', v)}
                min={5}
                max={252}
              />
              <NumberField
                label="Max leverage"
                value={form.maxLeverage}
                onChange={(v) => set('maxLeverage', v)}
                min={0.1}
                max={2}
                step={0.1}
              />
              <NumberField
                label="Rebalance at (%)"
                value={form.rebalance}
                onChange={(v) => set('rebalance', v)}
                min={0}
                max={100}
              />
            </>
          )}
          {form.type === 'macd' && (
            <>
              <NumberField
                label="Fast (bars)"
                value={form.macdFast}
                onChange={(v) => set('macdFast', v)}
                min={2}
                max={100}
              />
              <NumberField
                label="Slow (bars)"
                value={form.macdSlow}
                onChange={(v) => set('macdSlow', v)}
                min={3}
                max={250}
              />
              <NumberField
                label="Signal (bars)"
                value={form.macdSignal}
                onChange={(v) => set('macdSignal', v)}
                min={2}
                max={100}
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
          {form.type !== 'custom' && !LONG_ONLY.has(form.type) && (
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
            {run.isPending
              ? custom
                ? phase
                : 'Running…'
              : form.symbol
                ? `Backtest ${form.symbol}`
                : 'Backtest'}
          </button>
        </div>
        {about && <p className="disclaimer">{about}</p>}
        {run.error !== null && !(run.error instanceof StrategyRunError) && (
          <ErrorNotice error={run.error} />
        )}
      </form>

      {opened.error !== null && <ErrorNotice error={opened.error} />}
      {custom ? (
        <div className="strategy-workspace">
          <section className="card stack" style={{ gap: 12 }}>
            <div className="card-header">
              <div>
                <h2>Strategy code</h2>
                <div className="subtitle">{LANGUAGE_DETAILS[form.language].subtitle}</div>
              </div>
              <div className="filter-row">
                <Segmented
                  label="Language"
                  value={form.language}
                  options={LANGUAGE_OPTIONS}
                  onChange={chooseLanguage}
                />
                <button
                  type="button"
                  className="button ghost small"
                  disabled={run.isPending || form.code === starterCode(form.language)}
                  onClick={resetCode}
                >
                  Reset to template
                </button>
                {run.isPending ? (
                  <button
                    type="button"
                    className="button small"
                    onClick={() => stopper.current?.abort()}
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    type="submit"
                    form="backtest-form"
                    className="button primary small"
                    disabled={!form.symbol}
                  >
                    Run
                  </button>
                )}
              </div>
            </div>
            <Suspense fallback={<div className="empty">Loading the editor…</div>}>
              <StrategyEditor
                language={form.language}
                value={form.code}
                onChange={setCode}
                error={editorError}
                label={`${LANGUAGE_LABEL[form.language]} strategy code`}
              />
            </Suspense>
            <StrategyRunOutput error={runFailure} output={printed} language={form.language} />
          </section>
          {result ? (
            <ResultChart result={result} refreshing={run.isPending} />
          ) : (
            <section className="card">
              <div className="empty">
                Choose a symbol and run your strategy to see its equity curve against buying and
                holding.
              </div>
            </section>
          )}
        </div>
      ) : (
        result && <ResultChart result={result} refreshing={run.isPending} />
      )}
      {result && <ResultDetails result={result} />}

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
    case 'rsi':
      return `${strategy.period}-bar RSI, ${strategy.oversold}/${strategy.overbought}`
    case 'bollinger_bands':
      return `Bollinger ${strategy.mode}, ${strategy.window} bars, ${strategy.num_std}σ`
    case 'donchian_breakout':
      return `${strategy.entry}/${strategy.exit}-bar Donchian breakout`
    case 'dual_momentum':
      return `${strategy.lookback}-bar dual momentum against ${strategy.benchmark}`
    case 'volatility_targeting':
      return `Volatility target ${percent(strategy.target_volatility, 0)}, up to ${strategy.max_leverage}×`
    case 'macd':
      return `MACD ${strategy.fast}/${strategy.slow}/${strategy.signal}`
    case 'custom':
      return `Custom ${LANGUAGE_LABEL[strategy.language ?? 'python']} strategy`
    default:
      return 'Buy and hold'
  }
}

function ResultChart({ result, refreshing }: { result: BacktestResponse; refreshing: boolean }) {
  const theme = useChartTheme()
  const m = result.metrics
  const b = result.benchmark
  return (
    <ChartCard
      title={`${result.symbol}: ${describe(result.strategy)}`}
      subtitle={`${shortDate(m.start)} to ${shortDate(m.end)} · ${result.trades} trades · average exposure ${percent(result.exposure, 0)} · ${result.cost_bps} bps per trade`}
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
  )
}

function ResultDetails({ result }: { result: BacktestResponse }) {
  const m = result.metrics
  const b = result.benchmark
  return (
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
            <div className="subtitle">The strategy's daily returns regressed on factor returns</div>
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
