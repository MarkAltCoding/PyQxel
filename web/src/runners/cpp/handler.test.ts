import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Command } from '@yowasp/clang'
import type { BarColumns, UniverseColumns } from '../types'
import type {
  PositionsOutcome,
  WeightsOutcome,
  WorkerOutcome,
  WorkerRequest,
  WorkerResponse,
} from '../workerRunner'
import type { CompileResult } from './compile'
import { COMPILED_KEPT, createCppHandler, type CppWorkerParts } from './handler'
import { createCppRunner } from '.'

const BARS: BarColumns = {
  timestamps: ['2026-10-15T00:00:00-04:00', '2026-10-16T00:00:00-04:00'],
  open: [10, 11],
  high: [11, 12],
  low: [9, 10],
  close: [10.5, 11.5],
  volume: [100, null],
}
const CLANG = (() => undefined) as unknown as Command

/** A handler whose parts are fakes, with every call they get recorded in order. */
function setUp(overrides: Partial<CppWorkerParts> = {}) {
  const events: string[] = []
  const posted: WorkerResponse[] = []
  const parts: CppWorkerParts = {
    loadClang: vi.fn(async () => {
      events.push('load')
      return CLANG
    }),
    compile: vi.fn(async (_clang: Command, code: string): Promise<CompileResult> => {
      events.push(`compile ${code}`)
      return { ok: true, wasm: new Uint8Array([0, 97, 115, 109]), warnings: '' }
    }),
    instantiate: vi.fn(async () => ({}) as WebAssembly.Module),
    run: vi.fn(async (): Promise<PositionsOutcome> => {
      events.push('run')
      return { ok: true, positions: [1, 0], output: 'printed\n' }
    }),
    runUniverse: vi.fn(async (): Promise<WeightsOutcome> => {
      events.push('run universe')
      return { ok: true, weights: [[0.5, 0.5]], output: '' }
    }),
    post: (message) => {
      events.push(message.type === 'started' ? 'started' : 'result')
      posted.push(message)
    },
    ...overrides,
  }
  const handle = createCppHandler(parts)
  let id = 0
  const run = (code: string, bars: BarColumns | UniverseColumns = BARS) => {
    const request: WorkerRequest = { type: 'run', id: ++id, code, bars }
    return handle(request).then(() => posted.at(-1))
  }
  return { parts, events, posted, handle, run }
}

const outcomeOf = (message: WorkerResponse | undefined): WorkerOutcome => {
  if (message?.type !== 'result') throw new Error(`Expected a result, got ${message?.type}.`)
  return message.outcome
}

describe('the C++ worker’s message handling', () => {
  it('compiles across several symbols apart from one, and runs it on their bars', async () => {
    const { parts, run } = setUp()
    const universe: UniverseColumns = {
      symbols: ['GOOG', 'UNH'],
      dates: ['2026-10-16'],
      assets: [BARS, BARS].map(({ timestamps: _timestamps, ...columns }) => columns),
    }

    const single = outcomeOf(await run('int x;'))
    const across = outcomeOf(await run('int x;', universe))
    await run('int x;', universe)

    expect(single).toMatchObject({ ok: true, positions: [1, 0] })
    expect(across).toMatchObject({ ok: true, weights: [[0.5, 0.5]] })
    expect(vi.mocked(parts.compile).mock.calls.map((call) => call[2])).toEqual([false, true])
    expect(parts.runUniverse).toHaveBeenCalledWith(expect.anything(), universe)
  })

  it('loads the compiler once, on warming, for every later run', async () => {
    const { parts, handle, run } = setUp()

    await handle({ type: 'warm' })
    await run('a')
    await run('b')

    expect(parts.loadClang).toHaveBeenCalledOnce()
  })

  it('reports a compiler that fails to load, and loads it again on the next run', async () => {
    const loadClang = vi
      .fn<CppWorkerParts['loadClang']>()
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValue(CLANG)
    const { events, run } = setUp({ loadClang })

    const failed = outcomeOf(await run('a'))
    const retried = outcomeOf(await run('a'))

    expect(failed).toEqual({
      ok: false,
      error: {
        kind: 'load',
        message: 'The C++ compiler could not start: Error: Failed to fetch',
        line: null,
        traceback: '',
      },
      output: '',
    })
    expect(events.slice(0, 1)).toEqual(['result'])
    expect(retried.ok).toBe(true)
    expect(loadClang).toHaveBeenCalledTimes(2)
  })

  it('starts the run’s clock before compiling, since compiling runs code too', async () => {
    const { events, run } = setUp()

    await run('a')

    expect(events).toEqual(['load', 'started', 'compile a', 'run', 'result'])
  })

  it('passes a compile error through with its line, and runs nothing', async () => {
    const error = { kind: 'compile', message: "expected ';'", line: 7, traceback: 'tb' } as const
    const { parts, run } = setUp({ compile: vi.fn(async () => ({ ok: false, error }) as const) })

    expect(outcomeOf(await run('broken'))).toEqual({ ok: false, error, output: '' })
    expect(parts.instantiate).not.toHaveBeenCalled()
    expect(parts.run).not.toHaveBeenCalled()
  })

  it('puts the compiler’s warnings before what the strategy printed', async () => {
    const { run } = setUp({
      compile: vi.fn(async () => ({
        ok: true as const,
        wasm: new Uint8Array(),
        warnings: 'strategy.cpp:6:9: warning: using the result of an assignment',
      })),
    })

    expect(outcomeOf(await run('a'))).toEqual({
      ok: true,
      positions: [1, 0],
      output: 'strategy.cpp:6:9: warning: using the result of an assignment\n\nprinted\n',
    })
  })

  it('compiles code once, keeping the most recently used programs', async () => {
    const { parts, run } = setUp()
    const compiles = () => vi.mocked(parts.compile).mock.calls.map(([, code]) => code)

    await run('first')
    await run('first')
    expect(compiles()).toEqual(['first'])

    for (let n = 1; n < COMPILED_KEPT; n++) await run(`other ${n}`)
    await run('first')
    await run('newest')
    await run('first')
    expect(compiles().filter((code) => code === 'first')).toHaveLength(1)

    await run('other 1')
    expect(compiles().filter((code) => code === 'other 1')).toHaveLength(2)
  })

  it('turns an unexpected failure while running into a runtime error', async () => {
    const { run } = setUp({
      run: vi.fn(async () => {
        throw new TypeError('memory access out of bounds')
      }),
    })

    expect(outcomeOf(await run('a'))).toMatchObject({
      ok: false,
      error: { kind: 'runtime', message: 'TypeError: memory access out of bounds' },
    })
  })

  it('answers each run under its own id', async () => {
    const { posted, run } = setUp()

    await run('a')
    await run('b')

    expect(posted.map((message) => message.id)).toEqual([1, 1, 2, 2])
  })
})

/** Stands in for the C++ worker: records what it is sent and replies when told to. */
class FakeWorker extends EventTarget {
  static last: FakeWorker | null = null
  readonly sent: WorkerRequest[] = []

  constructor() {
    super()
    FakeWorker.last = this
  }

  postMessage(message: WorkerRequest): void {
    this.sent.push(message)
  }

  terminate(): void {}
}

describe('the C++ runner', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubGlobal('Worker', FakeWorker)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  const bars = [{ timestamp: '2026-10-16T00:00:00-04:00', close: 10 }]

  it('sends the code and bars to its own worker', async () => {
    const cpp = createCppRunner()
    void cpp.run('int x;', bars)
    await vi.advanceTimersByTimeAsync(0)

    expect(cpp.language).toBe('cpp')
    expect(FakeWorker.last?.sent).toMatchObject([
      { type: 'run', code: 'int x;', bars: { close: [10] } },
    ])
  })

  it('names the C++ compiler when it does not load in time or fails to start', async () => {
    const slow = createCppRunner().run('int x;', bars)
    await vi.advanceTimersByTimeAsync(180_000)
    const broken = createCppRunner().run('int x;', bars)
    await vi.advanceTimersByTimeAsync(0)
    FakeWorker.last!.dispatchEvent(new ErrorEvent('error'))

    await expect(slow).resolves.toMatchObject({
      error: {
        kind: 'load',
        message: expect.stringMatching(/^The C\+\+ compiler did not load within 180 seconds/),
      },
    })
    await expect(broken).resolves.toMatchObject({
      error: { kind: 'load', message: 'The C++ compiler failed to start.' },
    })
  })
})
