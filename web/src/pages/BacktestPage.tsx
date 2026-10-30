import { lazy, Suspense, useMemo, useRef, useState, type FormEvent } from 'react'
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
  runMultiAssetCustomBacktest,
} from '../api/endpoints'
import type {
  BacktestRequest,
  BacktestResponse,
  CodeLanguage,
  LastRun,
  Strategy,
  StrategyType,
} from '../api/types'
import { ChartCard, Legend } from '../charts/ChartCard'
import { EquityChart, EquityTable } from '../charts/EquityChart'
import { useChartTheme } from '../charts/theme'
import { AttributionCard, MetricsTable, NumberField } from '../components/BacktestParts'
import { CrossSectionalBacktest } from '../components/CrossSectionalBacktest'
import { ErrorNotice, Notice } from '../components/Notice'
import { SearchBox } from '../components/SearchBox'
import { Segmented } from '../components/Segmented'
import { InfoTip } from '../components/InfoTip'
import { Stat } from '../components/Stat'
import { StrategyAssistant } from '../components/StrategyAssistant'
import { StrategyRunOutput } from '../components/StrategyRunOutput'
import type { EditorError } from '../editor/StrategyEditor'
import type { EditorLanguage } from '../editor/languages'
import { starterCode, type CodeKind } from '../editor/starters'
import { dateTime, number, percent, shortDate, signedPercent, tone } from '../lib/format'
import { readStored, writeStored } from '../lib/storage'
import { runnerFor, StrategyRunError } from '../runners'
import { alignUniverse } from '../runners/types'
import { tickersIn } from '../lib/tickers'

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
  'Your own Python or C++, run in this browser. Only the positions it returns are sent to PyQxel, which backtests them like any built-in strategy. Add more symbols to allocate between them.'

const ACROSS_ABOUT =
  'Your own Python or C++, run in this browser, with no symbol to pick first: every ticker your code names in a string is fetched, on the dates they all traded. A ticker your weights leave out is only a signal. Only the weights are sent to PyQxel, which backtests them against holding the tickers they trade equally.'

const UNIVERSE_ABOUT =
  'Your own Python or C++, run in this browser, setting a weight for each symbol on every date they all traded. Only the weights are sent to PyQxel, which backtests them against holding the symbols equally.'

/** Most symbols a custom strategy may allocate between, as the API allows. */
const MAX_CUSTOM_SYMBOLS = 20

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

/** A stored backtest whose code the editor shows in place of the draft. */
interface Viewing {
  symbol: string
  savedAt: string | null
}

/** Where the code being written is kept between visits; each kind of strategy has a
 * draft of its own, since its code has a different shape. */
const draftKey = (language: EditorLanguage, kind: CodeKind) =>
  `pyqxel.strategyDraft.${language}${{ one: '', several: '.universe', across: '.across' }[kind]}`

/** The symbols a result was backtested on, as a title names them. */
const symbolsOf = (result: BacktestResponse) => (result.symbols ?? [result.symbol]).join(' + ')

/** Strategies that are long or flat only, so the short option does not apply. */
const LONG_ONLY = new Set<StrategyType>(['buy_and_hold', 'dual_momentum', 'volatility_targeting'])

interface Form {
  symbol: string
  /** Further symbols a custom strategy allocates between, beside `symbol`; across
   * tickers, those added beside the ones the code names. */
  extraSymbols: string[]
  /** Across tickers, tickers the code names that are left out of the run. */
  excluded: string[]
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
  extraSymbols: [],
  excluded: [],
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

type Mode = 'stock' | 'universe'

const MODES: { value: Mode; label: string }[] = [
  { value: 'stock', label: 'Single stock' },
  { value: 'universe', label: 'Across tickers' },
]

const SUBTITLES: Record<Mode, string> = {
  stock: 'Run a trading rule over history and compare it with buying and holding.',
  universe:
    'Rank a set of stocks against one another and hold the best, or write code that trades any tickers it names, and compare it with holding them all.',
}

type AcrossView = 'rank' | 'code'

const ACROSS_VIEWS: { value: AcrossView; label: string }[] = [
  { value: 'rank', label: 'Rank by a signal' },
  { value: 'code', label: 'Write your own code' },
]

export function BacktestPage() {
  const [params, setParams] = useSearchParams()
  const mode: Mode = params.get('mode') === 'universe' ? 'universe' : 'stock'
  const acrossView: AcrossView = params.get('view') === 'code' ? 'code' : 'rank'
  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Backtest</h1>
          <div className="subtitle">{SUBTITLES[mode]}</div>
        </div>
        <Segmented
          label="What to backtest"
          value={mode}
          options={MODES}
          onChange={(next) => setParams(next === 'universe' ? { mode: 'universe' } : {})}
        />
      </header>
      {mode === 'universe' && (
        <Segmented
          label="How to choose holdings"
          value={acrossView}
          options={ACROSS_VIEWS}
          onChange={(next) =>
            setParams({ mode: 'universe', ...(next === 'code' && { view: 'code' }) })
          }
        />
      )}
      {mode === 'stock' ? (
        <SingleStockBacktest />
      ) : acrossView === 'code' ? (
        <SingleStockBacktest across />
      ) : (
        <CrossSectionalBacktest />
      )}
    </div>
  )
}

/**
 * A backtest of one symbol, with a built-in rule or custom code; or, `across`, of custom
 * code alone, trading every ticker it names with no symbol to pick first.
 */
function SingleStockBacktest({ across = false }: { across?: boolean }) {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const openId = params.get('id')
  /** What a custom strategy in this form trades. */
  const kindOf = (current: Form): CodeKind =>
    across ? 'across' : current.extraSymbols.length > 0 ? 'several' : 'one'
  const [form, setForm] = useState<Form>(() => ({
    ...DEFAULTS,
    ...(across && { type: 'custom' as const }),
    symbol: params.get('symbol')?.toUpperCase() ?? '',
    code:
      readStored(draftKey(DEFAULTS.language, across ? 'across' : 'one')) ??
      starterCode(DEFAULTS.language, across ? 'across' : 'one'),
  }))
  const set = <K extends keyof Form>(key: K, value: Form[K]) =>
    setForm((current) => ({ ...current, [key]: value }))
  const custom = across || form.type === 'custom'
  const kind = kindOf(form)
  /** Whether the custom strategy allocates between several symbols. */
  const universe = custom && kind !== 'one'
  /** Across tickers, the tickers the code names, which the run fetches. */
  const named = useMemo(
    () => (across ? tickersIn(form.code, form.language) : []),
    [across, form.code, form.language],
  )
  const symbols = across
    ? [...new Set([...named, ...form.extraSymbols])].filter((s) => !form.excluded.includes(s))
    : universe
      ? [form.symbol, ...form.extraSymbols]
      : [form.symbol]
  /** Why the run cannot start yet, if it cannot. */
  const blocked = across
    ? symbols.length < 2
      ? 'Name at least two tickers in your code, such as SYMBOLS = ["SPY", "TLT"], or add them above.'
      : symbols.length > MAX_CUSTOM_SYMBOLS
        ? `At most ${MAX_CUSTOM_SYMBOLS} tickers can be backtested at once; this code names ${symbols.length}. Drop some with ×.`
        : null
    : form.symbol
      ? null
      : 'Choose a symbol.'

  /** The stored backtest whose code is in the editor instead of the draft, if any. Its
   * code and edits to it are never saved over the draft unless the user asks. */
  const [viewing, setViewing] = useState<Viewing | null>(null)
  const draftOf = (language: EditorLanguage, codeKind: CodeKind) =>
    readStored(draftKey(language, codeKind)) ?? starterCode(language, codeKind)
  const setCode = (code: string) => {
    set('code', code)
    if (viewing === null) writeStored(draftKey(form.language, kind), code)
  }
  /** Puts the language's draft back in the editor, leaving the stored backtest's code. */
  const backToDraft = () => {
    setForm((current) => ({ ...current, code: draftOf(current.language, kindOf(current)) }))
    setViewing(null)
  }
  /** Makes the code in the editor the draft, replacing the draft there was. */
  const keepAsDraft = () => {
    writeStored(draftKey(form.language, kind), form.code)
    setViewing(null)
  }
  /** Sets the symbols traded beside the first. Going from one symbol to several, or back,
   * swaps in that kind of strategy's draft, unless a stored backtest's code is shown. */
  const setExtraSymbols = (extraSymbols: string[]) => {
    const several = extraSymbols.length > 0
    setForm((current) => ({
      ...current,
      extraSymbols,
      code:
        !across && viewing === null && several !== current.extraSymbols.length > 0
          ? draftOf(current.language, several ? 'several' : 'one')
          : current.code,
    }))
  }
  /** Across tickers: adds a ticker beside those the code names, or brings back one left
   * out. */
  const addTicker = (symbol: string) =>
    setForm((current) => ({
      ...current,
      excluded: current.excluded.filter((s) => s !== symbol),
      extraSymbols:
        named.includes(symbol) || current.extraSymbols.includes(symbol)
          ? current.extraSymbols
          : [...current.extraSymbols, symbol],
    }))
  /** Across tickers: leaves a ticker out of the run, whether the code names it or not. */
  const dropTicker = (symbol: string) =>
    setForm((current) => ({
      ...current,
      extraSymbols: current.extraSymbols.filter((s) => s !== symbol),
      excluded: named.includes(symbol) ? [...current.excluded, symbol] : current.excluded,
    }))
  /** Switches the custom strategy's language, to that language's own draft or starter. */
  const chooseLanguage = (language: EditorLanguage) => {
    if (language === form.language) return
    setViewing(null)
    setForm((current) => ({
      ...current,
      language,
      code: draftOf(language, kindOf(current)),
    }))
    // Its runtime starts downloading while the code is read and written.
    runnerFor(language).warm()
  }
  /** What a custom run is doing, for the Run button. */
  const [phase, setPhase] = useState('')
  /** What the last custom run printed. */
  const [printed, setPrinted] = useState('')
  /** The code the last custom run ran, so its outcome is told to Claude only while the
   * editor still holds that code. */
  const [ranCode, setRanCode] = useState<string | null>(null)
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
      if (!custom) {
        return runBacktest(form.symbol, { strategy: strategyOf(form), ...options })
      }
      if (universe) return runAcross(options)
      // The code runs here, in a worker; the server only sees and scores its positions.
      const controller = new AbortController()
      stopper.current = controller
      try {
        setPrinted('')
        setRanCode(form.code)
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
        // A rerun of a stored backtest's code is what the editor now shows.
        if (viewing !== null)
          setViewing({ symbol: symbolsOf(result), savedAt: result.saved_at ?? null })
        setLoadedId(result.id)
        queryClient.setQueryData(['backtest', result.id], result)
        setParams(across ? { mode: 'universe', view: 'code', id: result.id } : { id: result.id })
      }
    },
  })
  /** Runs the custom strategy across every symbol in this browser, and has the server
   * score the weights it set. */
  async function runAcross(options: {
    period: Form['period']
    interval: Form['interval']
    cost_bps: number
    cash_interest: boolean
  }): Promise<BacktestResponse> {
    const controller = new AbortController()
    stopper.current = controller
    try {
      setPrinted('')
      setRanCode(form.code)
      setPhase('Fetching prices…')
      const histories = await Promise.all(
        symbols.map(async (symbol) => {
          try {
            return { symbol, bars: (await getHistory(symbol, form.period, form.interval)).bars }
          } catch (error) {
            if (across && error instanceof ApiError && error.status === 404) {
              throw new ApiError(
                404,
                `${symbol} is not a ticker PyQxel can find. If your code names it as something else, leave it out with its ×; otherwise check the spelling.`,
              )
            }
            throw error
          }
        }),
      )
      const empty = histories.find((history) => history.bars.length === 0)
      if (empty) throw new ApiError(422, `${empty.symbol} has no prices in this window.`)
      const aligned = alignUniverse(histories)
      if (aligned.dates.length === 0) {
        throw new ApiError(422, `${symbols.join(', ')} share no trading days in this window.`)
      }
      const details = LANGUAGE_DETAILS[form.language]
      setPhase(details.loading)
      const outcome = await runnerFor(form.language).runUniverse(form.code, aligned, {
        signal: controller.signal,
        onStart: () => setPhase(details.running),
      })
      setPrinted(outcome.output)
      if (!outcome.ok) throw new StrategyRunError(outcome.error, outcome.output)
      setPhase('Scoring…')
      return await runMultiAssetCustomBacktest({
        ...options,
        symbols,
        code: form.code,
        language: form.language,
        dates: outcome.dates,
        weights: outcome.weights,
      })
    } finally {
      stopper.current = null
    }
  }
  const remove = useMutation({
    mutationFn: deleteBacktest,
    onSuccess: (_, id) => {
      void queryClient.invalidateQueries({ queryKey: ['backtests'] })
      if (id === openId) setParams({})
    },
  })

  // Opening a stored custom backtest puts its code and settings back in the form, so it
  // can be rerun as it was or edited, without touching the draft. This adjusts state while
  // rendering, once per opened backtest, as React recommends for state that follows
  // other data.
  const openedResult = opened.data
  if (openedResult?.id && openedResult.id !== loadedId) {
    setLoadedId(openedResult.id)
    const { strategy } = openedResult
    if (strategy.type === 'custom') {
      const language = strategy.language ?? 'python'
      const traded = openedResult.symbols ?? [openedResult.symbol]
      const [first, ...rest] = traded
      const openedKind: CodeKind = across ? 'across' : rest.length > 0 ? 'several' : 'one'
      // Across tickers, the run's symbols are what the code names, give or take some.
      const codeNames = across ? tickersIn(strategy.code, language) : []
      setViewing(
        strategy.code === draftOf(language, openedKind)
          ? null
          : { symbol: symbolsOf(openedResult), savedAt: openedResult.saved_at ?? null },
      )
      setForm((current) => ({
        ...current,
        type: 'custom',
        language,
        code: strategy.code,
        symbol: across ? '' : (first ?? openedResult.symbol),
        extraSymbols: across ? traded.filter((s) => !codeNames.includes(s)) : rest,
        excluded: across ? codeNames.filter((s) => !traded.includes(s)) : [],
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
    if (!blocked) run.mutate()
  }
  const strategy = STRATEGIES.find((s) => s.value === form.type)
  const about = across
    ? ACROSS_ABOUT
    : universe
      ? UNIVERSE_ABOUT
      : custom
        ? CUSTOM_ABOUT
        : strategy?.about
  // Failures in the browser are shown under the code; the rest, under the form.
  const runFailure = run.error instanceof StrategyRunError ? run.error.failure : null
  const editorError = useMemo<EditorError | null>(
    () =>
      runFailure && runFailure.kind !== 'cancelled'
        ? { line: runFailure.line, message: runFailure.message }
        : null,
    [runFailure],
  )
  const lastRun = useMemo<LastRun | null>(() => {
    if (!custom || ranCode !== form.code) return null
    const output = printed.slice(-4_000) || null
    if (runFailure) {
      if (runFailure.kind === 'cancelled' || runFailure.kind === 'load') return null
      return {
        error_kind: runFailure.kind,
        message: runFailure.message.slice(0, 4_000),
        line: runFailure.line,
        traceback: runFailure.traceback.slice(-4_000) || null,
        output,
      }
    }
    return run.isSuccess ? { error_kind: null, output } : null
  }, [custom, ranCode, form.code, printed, runFailure, run.isSuccess])
  const resetCode = () => {
    if (window.confirm('Replace your code with the starting template?')) {
      setCode(starterCode(form.language, kind))
    }
  }

  return (
    <>
      <form id="backtest-form" className="card stack" style={{ gap: 14 }} onSubmit={submit}>
        <div className="filter-row">
          {across && (
            <div className="field" style={{ width: 220 }}>
              <span>Add a ticker</span>
              <SearchBox
                label="Add a ticker"
                clearOnSelect
                placeholder="Beside those your code names"
                onSelect={addTicker}
              />
            </div>
          )}
          {!across && (
            <div className="field" style={{ width: 220 }}>
              <span>Symbol</span>
              <SearchBox
                label="Symbol"
                placeholder={form.symbol || 'Ticker or company'}
                onSelect={(symbol) => {
                  set('symbol', symbol)
                  if (form.extraSymbols.includes(symbol)) {
                    setExtraSymbols(form.extraSymbols.filter((extra) => extra !== symbol))
                  }
                }}
              />
            </div>
          )}
          {custom && !across && (
            <div className="field" style={{ width: 220 }}>
              <span>Also trade</span>
              <SearchBox
                label="Also trade"
                clearOnSelect
                placeholder={
                  form.extraSymbols.length + 1 >= MAX_CUSTOM_SYMBOLS
                    ? `At most ${MAX_CUSTOM_SYMBOLS} symbols`
                    : 'Add a symbol to allocate between'
                }
                onSelect={(symbol) => {
                  if (
                    symbol !== form.symbol &&
                    !form.extraSymbols.includes(symbol) &&
                    form.extraSymbols.length + 1 < MAX_CUSTOM_SYMBOLS
                  ) {
                    setExtraSymbols([...form.extraSymbols, symbol])
                  }
                }}
              />
            </div>
          )}
          {!across && (
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
          )}
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
            disabled={Boolean(blocked) || run.isPending}
            style={{ marginLeft: 'auto' }}
          >
            {run.isPending
              ? custom
                ? phase
                : 'Running…'
              : blocked
                ? 'Backtest'
                : across && symbols.length > 3
                  ? `Backtest ${symbols.length} tickers`
                  : `Backtest ${symbols.join(' + ')}`}
          </button>
        </div>
        {across && (
          <div className="stack" style={{ gap: 8 }}>
            <div
              className="filter-row"
              aria-label="Tickers fetched"
              style={{ alignItems: 'center' }}
            >
              <span className="muted">Tickers fetched:</span>
              {symbols.length === 0 && <span className="muted">none yet</span>}
              {symbols.map((symbol) => (
                <span key={symbol} className="badge">
                  {symbol}
                  {!named.includes(symbol) && <span className="muted"> · added</span>}
                  <button
                    type="button"
                    className="button ghost small"
                    aria-label={`Leave out ${symbol}`}
                    onClick={() => dropTicker(symbol)}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            {form.excluded.length > 0 && (
              <div
                className="filter-row"
                aria-label="Tickers left out"
                style={{ alignItems: 'center' }}
              >
                <span className="muted">Left out:</span>
                {form.excluded.map((symbol) => (
                  <button
                    key={symbol}
                    type="button"
                    className="button ghost small"
                    aria-label={`Bring back ${symbol}`}
                    onClick={() => addTicker(symbol)}
                  >
                    {symbol} ↺
                  </button>
                ))}
              </div>
            )}
            {blocked && <small className="muted">{blocked}</small>}
          </div>
        )}
        {universe && !across && (
          <div className="filter-row" aria-label="Symbols traded">
            {symbols.map((symbol, index) => (
              <span key={symbol} className="badge">
                {symbol}
                {index > 0 && (
                  <button
                    type="button"
                    className="button ghost small"
                    aria-label={`Stop trading ${symbol}`}
                    onClick={() =>
                      setExtraSymbols(form.extraSymbols.filter((extra) => extra !== symbol))
                    }
                  >
                    ×
                  </button>
                )}
              </span>
            ))}
          </div>
        )}
        {about && <p className="disclaimer">{about}</p>}
        {run.error !== null && !(run.error instanceof StrategyRunError) && (
          <ErrorNotice error={run.error} />
        )}
      </form>

      {opened.error !== null && <ErrorNotice error={opened.error} />}
      {custom ? (
        <div className="strategy-workspace">
          <section id="strategy-code" className="card stack" style={{ gap: 12 }}>
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
                  disabled={run.isPending || form.code === starterCode(form.language, kind)}
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
                    disabled={Boolean(blocked)}
                  >
                    Run
                  </button>
                )}
              </div>
            </div>
            {viewing && (
              <Notice>
                <div className="filter-row" style={{ justifyContent: 'space-between' }}>
                  <span>
                    This is the code of the {viewing.symbol} backtest
                    {viewing.savedAt ? ` saved ${dateTime(viewing.savedAt)}` : ''}. Your draft is
                    kept: editing here does not change it.
                  </span>
                  <span className="filter-row">
                    <button type="button" className="button ghost small" onClick={backToDraft}>
                      Back to my draft
                    </button>
                    <button type="button" className="button ghost small" onClick={keepAsDraft}>
                      Use this as my draft
                    </button>
                  </span>
                </div>
              </Notice>
            )}
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
            <StrategyAssistant
              key={form.language}
              language={form.language}
              code={form.code}
              symbol={form.symbol}
              symbols={symbols}
              interval={form.interval}
              lastRun={lastRun}
              onApply={setCode}
            />
          </section>
          {result ? (
            <ResultChart result={result} refreshing={run.isPending} />
          ) : (
            <section id="result" className="card">
              <div className="empty">
                {across
                  ? 'Name the tickers you want in your code, and run it to see its equity curve against holding the ones it trades equally.'
                  : 'Choose a symbol, or several to allocate between, and run your strategy to see its equity curve against buying and holding.'}
              </div>
            </section>
          )}
        </div>
      ) : (
        result && <ResultChart result={result} refreshing={run.isPending} />
      )}
      {result && <ResultDetails result={result} />}

      <section id="saved-backtests" className="card">
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
                  <th className="num">Benchmark</th>
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
                        {(item.symbols ?? [item.symbol]).join(' + ')}
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
    </>
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
  const benchmark = benchmarkLabel(result)
  return (
    <ChartCard
      id="result"
      info="equityChart"
      title={`${symbolsOf(result)}: ${describe(result.strategy)}`}
      subtitle={`${shortDate(m.start)} to ${shortDate(m.end)} · ${result.trades} trades · average exposure ${percent(result.exposure, 0)} · ${result.cost_bps} bps per trade`}
      refreshing={refreshing}
      legend={
        <Legend
          items={[
            { label: 'Strategy', color: theme.series1 },
            { label: benchmark, color: theme.series2 },
          ]}
        />
      }
      chart={
        <EquityChart
          points={result.equity_curve}
          benchmarkLabel={benchmark}
          benchmarkShort={result.symbols ? 'Equal' : 'Hold'}
        />
      }
      table={<EquityTable points={result.equity_curve} benchmarkLabel={benchmark} />}
      footer={
        <div className="stack" style={{ gap: 12, marginTop: 14 }}>
          <div className="stats">
            <Stat
              info="totalReturn"
              label="Total return"
              value={signedPercent(m.total_return)}
              className={tone(m.total_return)}
              detail={`${benchmark} ${signedPercent(b.total_return)}`}
            />
            <Stat
              info="annualizedReturn"
              label="Annualized"
              value={signedPercent(m.annualized_return)}
              detail={`${benchmark} ${signedPercent(b.annualized_return)}`}
            />
            <Stat
              info="sharpe"
              label="Sharpe ratio"
              value={number(m.sharpe_ratio)}
              detail={`${benchmark} ${number(b.sharpe_ratio)}`}
            />
            <Stat
              info="maxDrawdown"
              label="Max drawdown"
              value={percent(m.max_drawdown)}
              detail={`${benchmark} ${percent(b.max_drawdown)}`}
            />
          </div>
          {result.notice && <Notice tone="warning">{result.notice}</Notice>}
          <p className="disclaimer">{result.disclaimer}</p>
        </div>
      }
    />
  )
}

/** What a result is compared with: buying and holding its symbol, or holding several
 * equally. */
const benchmarkLabel = (result: BacktestResponse) =>
  result.symbols ? 'Equal weight' : 'Buy and hold'

function ResultDetails({ result }: { result: BacktestResponse }) {
  return (
    <>
      <div className="grid-2">
        <section id="performance" className="card">
          <div className="card-header">
            <div className="heading-with-info">
              <h2>Performance</h2>
              <InfoTip topic="backtestPerformance" />
            </div>
          </div>
          <MetricsTable
            strategy={result.metrics}
            benchmark={result.benchmark}
            benchmarkLabel={benchmarkLabel(result)}
          />
        </section>
        <AttributionCard attribution={result.attribution} />
      </div>
      {result.assets && (
        <section id="allocation" className="card">
          <div className="card-header">
            <div>
              <h2>Allocation</h2>
              <div className="subtitle">How the strategy held each symbol over the backtest</div>
            </div>
          </div>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th className="num">
                    Average weight
                    <InfoTip topic="averageWeight" />
                  </th>
                  <th className="num">
                    Held
                    <InfoTip topic="timeHeld" />
                  </th>
                  <th className="num">
                    Trades
                    <InfoTip topic="trades" />
                  </th>
                  <th className="num">
                    Contribution
                    <InfoTip topic="contribution" />
                  </th>
                </tr>
              </thead>
              <tbody>
                {result.assets.map((asset) => (
                  <tr key={asset.symbol}>
                    <td>{asset.symbol}</td>
                    <td className="num">{signedPercent(asset.average_weight, 0)}</td>
                    <td className="num">{percent(asset.exposure, 0)}</td>
                    <td className="num">{asset.trades}</td>
                    <td className={`num ${tone(asset.contribution) ?? ''}`}>
                      {signedPercent(asset.contribution)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  )
}
