import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { forEachDiagnostic } from '@codemirror/lint'
import { EditorView } from '@codemirror/view'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { BacktestResponse, BacktestSummary, OHLCVBar } from '../api/types'
import { starterCode } from '../editor/starters'
import type { RunOptions, RunResult } from '../runners/types'
import { BacktestPage } from './BacktestPage'

// The worker is replaced by a fake runner; everything else, the editor included, is real.
const runner = vi.hoisted(() => ({
  language: 'python' as const,
  run: vi.fn(),
  warm: vi.fn(),
  dispose: vi.fn(),
  /** The languages the page asked for a runner in, in order. */
  asked: [] as string[],
}))
vi.mock('../runners', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../runners')>()),
  runnerFor: (language: string) => {
    runner.asked.push(language)
    return runner
  },
}))
// ECharts needs a real layout; the chart itself is not under test here.
vi.mock('../charts/EChart', () => ({
  EChart: ({ label }: { label: string }) => <div role="img" aria-label={label} />,
}))

const BARS: OHLCVBar[] = [
  { timestamp: '2026-10-14T00:00:00-04:00', open: 10, high: 11, low: 9, close: 10, volume: 5 },
  { timestamp: '2026-10-15T00:00:00-04:00', open: 10, high: 12, low: 10, close: 11, volume: 6 },
  { timestamp: '2026-10-16T00:00:00-04:00', open: 11, high: 12, low: 10, close: 12, volume: 7 },
]

const METRICS = {
  start: '2026-10-14T00:00:00-04:00',
  end: '2026-10-16T00:00:00-04:00',
  observations: 2,
  total_return: 0.1,
  annualized_volatility: 0.2,
  max_drawdown: -0.05,
}

function result(overrides: Partial<BacktestResponse> = {}): BacktestResponse {
  return {
    id: 'run-1',
    saved_at: '2026-10-18T09:00:00Z',
    symbol: 'SPY',
    period: '10y',
    interval: '1d',
    periods_per_year: 252,
    strategy: { type: 'custom', language: 'python', code: 'def strategy(bars):\n    return 1\n' },
    cost_bps: 5,
    risk_free_rate: 0,
    metrics: METRICS,
    benchmark: METRICS,
    trades: 1,
    exposure: 1,
    cash_interest: true,
    equity_curve: BARS.map((bar) => ({ timestamp: bar.timestamp, strategy: 1, benchmark: 1 })),
    coverage: 'full',
    disclaimer: 'Backtested on historical adjusted prices.',
    ...overrides,
  }
}

const summary = (overrides: Partial<BacktestSummary>): BacktestSummary => ({
  id: 'run-1',
  saved_at: '2026-10-18T09:00:00Z',
  symbol: 'SPY',
  strategy: 'custom',
  period: '10y',
  interval: '1d',
  total_return: 0.1,
  sharpe_ratio: 1,
  max_drawdown: -0.05,
  benchmark_total_return: 0.2,
  ...overrides,
})

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

/** Answers the page's API calls from `routes`, keyed by method and path, and records them. */
function serve(routes: Record<string, (body: unknown) => unknown>) {
  const calls: { method: string; path: string; body: unknown }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path: url.pathname, body })
    const handler = routes[`${method} ${url.pathname}`]
    if (!handler) return json({ detail: `No fake for ${method} ${url.pathname}` }, 404)
    return json(handler(body))
  })
  return calls
}

const NO_SAVED = { 'GET /api/v1/backtests': () => ({ items: [], total: 0, limit: 50, offset: 0 }) }

function renderPage(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter([{ path: '/backtest', element: <BacktestPage /> }], {
    initialEntries: [path],
  })
  render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return router
}

/** The strategy menu, found by the option it starts on. */
const strategyMenu = () => screen.getByDisplayValue<HTMLSelectElement>('Moving-average crossover')

/** The CodeMirror view inside the page, once the lazily loaded editor has appeared. */
async function editor(): Promise<EditorView> {
  const element = await waitFor(() => {
    const found = document.querySelector<HTMLElement>('.cm-editor')
    expect(found).not.toBeNull()
    return found!
  })
  return EditorView.findFromDOM(element)!
}

const marks = (view: EditorView) => {
  const lines: number[] = []
  forEachDiagnostic(view.state, (_, from) => lines.push(view.state.doc.lineAt(from).number))
  return lines
}

beforeEach(() => {
  runner.run.mockReset()
  runner.warm.mockReset()
  runner.asked.length = 0
})

afterEach(() => vi.restoreAllMocks())

describe('custom strategies on the Backtest page', () => {
  it('runs the code in the browser and posts its positions with the code and language', async () => {
    const calls = serve({
      ...NO_SAVED,
      'GET /api/v1/stocks/SPY/history': () => ({
        symbol: 'SPY',
        period: '10y',
        interval: '1d',
        bars: BARS,
        coverage: 'full',
      }),
      'POST /api/v1/stocks/SPY/backtest/custom': () => result(),
    })
    runner.run.mockImplementation(
      async (_code: string, bars: OHLCVBar[], options?: RunOptions): Promise<RunResult> => {
        options?.onStart?.()
        return {
          ok: true,
          timestamps: bars.map((bar) => bar.timestamp),
          positions: [0, 1, 1],
          output: 'hello\n',
        }
      },
    )
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    expect(runner.warm).toHaveBeenCalledOnce()
    const view = await editor()
    expect(view.state.doc.toString()).toBe(starterCode('python'))
    await userEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(
      await screen.findByRole('heading', { name: 'SPY: Custom Python strategy' }),
    ).toBeInTheDocument()
    expect(runner.run).toHaveBeenCalledWith(starterCode('python'), BARS, expect.any(Object))
    const posted = calls.find((call) => call.path === '/api/v1/stocks/SPY/backtest/custom')
    expect(posted).toEqual({
      method: 'POST',
      path: '/api/v1/stocks/SPY/backtest/custom',
      body: {
        period: '10y',
        interval: '1d',
        cost_bps: 5,
        cash_interest: true,
        code: starterCode('python'),
        language: 'python',
        timestamps: BARS.map((bar) => bar.timestamp),
        positions: [0, 1, 1],
      },
    })
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /Buy and hold/ })).not.toBeInTheDocument()
  })

  it('shows a failing run under the code, marks its line, and posts nothing', async () => {
    const calls = serve({
      ...NO_SAVED,
      'GET /api/v1/stocks/SPY/history': () => ({
        symbol: 'SPY',
        period: '10y',
        interval: '1d',
        bars: BARS,
        coverage: 'full',
      }),
    })
    runner.run.mockResolvedValue({
      ok: false,
      error: {
        kind: 'runtime',
        message: "KeyError: 'Clse'",
        line: 3,
        traceback: 'Traceback (most recent call last):\n  File "<strategy>", line 3\nKeyError',
      },
      output: 'rows 3\n',
    } satisfies RunResult)
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    const view = await editor()
    await userEvent.click(screen.getByRole('button', { name: 'Run' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Error on line 3')
    expect(alert).toHaveTextContent('File "<strategy>", line 3')
    expect(alert.closest('.strategy-workspace')).not.toBeNull()
    expect(screen.getByText('rows 3')).toBeInTheDocument()
    expect(marks(view)).toEqual([3])
    expect(calls.some((call) => call.path.endsWith('/backtest/custom'))).toBe(false)
  })

  it('reloads a saved custom run’s code and settings into the editor', async () => {
    const code = 'def strategy(bars):\n    return bars["Close"] * 0 + 0.5\n'
    serve({
      'GET /api/v1/backtests': () => ({
        items: [summary({ id: 'saved-run' })],
        total: 1,
        limit: 50,
        offset: 0,
      }),
      'GET /api/v1/backtests/saved-run': () =>
        result({
          id: 'saved-run',
          symbol: 'QQQ',
          period: '5y',
          cost_bps: 12,
          strategy: { type: 'custom', language: 'python', code },
        }),
    })
    renderPage('/backtest?id=saved-run')

    expect(
      await screen.findByRole('heading', { name: 'QQQ: Custom Python strategy' }),
    ).toBeInTheDocument()
    const view = await editor()
    expect(view.state.doc.toString()).toBe(code)
    expect(screen.getByDisplayValue('Custom code (Python or C++)')).toBeInTheDocument()
    expect(screen.getByDisplayValue('5 years')).toBeInTheDocument()
    expect(screen.getByDisplayValue('12')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Backtest QQQ' })).toBeInTheDocument()
  })

  it('labels custom results and saved rows as custom, never as buy and hold', async () => {
    serve({
      'GET /api/v1/backtests': () => ({
        items: [
          summary({ id: 'custom-run' }),
          summary({ id: 'hold-run', strategy: 'buy_and_hold', symbol: 'IWM' }),
        ],
        total: 2,
        limit: 50,
        offset: 0,
      }),
      'GET /api/v1/backtests/custom-run': () => result({ id: 'custom-run' }),
    })
    renderPage('/backtest?id=custom-run')

    expect(
      await screen.findByRole('heading', { name: 'SPY: Custom Python strategy' }),
    ).toBeInTheDocument()
    const savedList = screen.getByRole('heading', { name: 'Saved backtests' }).closest('section')!
    const rows = within(within(savedList).getByRole('table')).getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getAllByRole('cell')[1]!.textContent)).toEqual([
      'Custom',
      'Buy and hold',
    ])
    expect(screen.queryByRole('heading', { name: /: Buy and hold/ })).not.toBeInTheDocument()
  })

  it('keeps what is typed as a draft for the next visit', async () => {
    serve(NO_SAVED)
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    const view = await editor()
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'x = 1\n' } })

    expect(window.localStorage.getItem('pyqxel.strategyDraft.python')).toBe('x = 1\n')
  })

  it('switches languages, each with its own draft, and starts loading the chosen one', async () => {
    serve(NO_SAVED)
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    const view = await editor()
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'x = 1\n' } })
    await userEvent.click(screen.getByRole('button', { name: 'C++' }))

    expect(view.state.doc.toString()).toBe(starterCode('cpp'))
    expect(document.querySelector('[data-language="cpp"]')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'C++' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByText(/C\+\+20 with its standard library/)).toBeInTheDocument()
    expect(runner.asked).toEqual(['python', 'cpp'])
    expect(runner.warm).toHaveBeenCalledTimes(2)

    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'int x;\n' } })
    await userEvent.click(screen.getByRole('button', { name: 'Python' }))

    expect(view.state.doc.toString()).toBe('x = 1\n')
    expect(window.localStorage.getItem('pyqxel.strategyDraft.python')).toBe('x = 1\n')
    expect(window.localStorage.getItem('pyqxel.strategyDraft.cpp')).toBe('int x;\n')
  })

  it('runs C++ in its runner and posts the code with language cpp', async () => {
    const calls = serve({
      ...NO_SAVED,
      'GET /api/v1/stocks/SPY/history': () => ({
        symbol: 'SPY',
        period: '10y',
        interval: '1d',
        bars: BARS,
        coverage: 'full',
      }),
      'POST /api/v1/stocks/SPY/backtest/custom': (body) => {
        const { code, language } = body as { code: string; language: 'cpp' }
        return result({ strategy: { type: 'custom', language, code } })
      },
    })
    runner.run.mockImplementation(async (_code: string, bars: OHLCVBar[]): Promise<RunResult> => ({
      ok: true,
      timestamps: bars.map((bar) => bar.timestamp),
      positions: [1, 1, 0],
      output: '',
    }))
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    await editor()
    await userEvent.click(screen.getByRole('button', { name: 'C++' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(
      await screen.findByRole('heading', { name: 'SPY: Custom C++ strategy' }),
    ).toBeInTheDocument()
    expect(runner.asked.at(-1)).toBe('cpp')
    expect(runner.run).toHaveBeenCalledWith(starterCode('cpp'), BARS, expect.any(Object))
    const posted = calls.find((call) => call.path === '/api/v1/stocks/SPY/backtest/custom')
    expect(posted?.body).toMatchObject({
      code: starterCode('cpp'),
      language: 'cpp',
      positions: [1, 1, 0],
    })
  })

  it('reopens a saved C++ run in C++', async () => {
    const code =
      '#include <pyqxel.h>\nvoid strategy(const pyqxel::Bars& b, std::span<double> p) {}\n'
    serve({
      ...NO_SAVED,
      'GET /api/v1/backtests/cpp-run': () =>
        result({ id: 'cpp-run', strategy: { type: 'custom', language: 'cpp', code } }),
    })
    renderPage('/backtest?id=cpp-run')

    expect(
      await screen.findByRole('heading', { name: 'SPY: Custom C++ strategy' }),
    ).toBeInTheDocument()
    const view = await editor()
    expect(view.state.doc.toString()).toBe(code)
    expect(document.querySelector('[data-language="cpp"]')).not.toBeNull()
    expect(screen.getByRole('button', { name: 'C++' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('names the runtime that could not start', async () => {
    serve({
      ...NO_SAVED,
      'GET /api/v1/stocks/SPY/history': () => ({
        symbol: 'SPY',
        period: '10y',
        interval: '1d',
        bars: BARS,
        coverage: 'full',
      }),
    })
    runner.run.mockResolvedValue({
      ok: false,
      error: { kind: 'load', message: 'Failed to fetch', line: null, traceback: '' },
      output: '',
    } satisfies RunResult)
    renderPage('/backtest?symbol=SPY')

    await userEvent.selectOptions(strategyMenu(), 'custom')
    await editor()
    await userEvent.click(screen.getByRole('button', { name: 'C++' }))
    await userEvent.click(screen.getByRole('button', { name: 'Run' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('The C++ compiler could not start')
  })
})
