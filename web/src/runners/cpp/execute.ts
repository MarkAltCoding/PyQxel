// Runs a compiled C++ strategy on the bars and checks the positions it set, on the WASI
// shim with no files, no network and an empty stdin: only stdout and stderr reach out.
import { ConsoleStdout, File, OpenFile, WASI, WASIProcExit } from '@bjorn3/browser_wasi_shim'
import type { BarColumns } from '../types'
import type { WorkerOutcome } from '../workerRunner'

/** Largest position, long or short, as a multiple of capital; the server checks it too. */
export const MAX_POSITION = 2
/** Most characters of printed output passed back. */
export const MAX_OUTPUT = 20_000

/** What the entry point (entry.cpp) exports. */
interface StrategyModule {
  memory: WebAssembly.Memory
  _initialize: () => void
  pyqxel_allocate: (size: number) => void
  pyqxel_column: (column: number) => number
  pyqxel_timestamps: () => number
  pyqxel_run: () => void
}

/** The column numbers entry.cpp gives open, high, low, close, volume and positions. */
const COLUMNS = ['open', 'high', 'low', 'close', 'volume'] as const
const POSITIONS = 5

const failure = (kind: 'runtime' | 'result', message: string, output: string): WorkerOutcome => ({
  ok: false,
  error: { kind, message, line: null, traceback: message },
  output,
})

/** The positions as numbers, or why they cannot be backtested, naming the first bad bar
 * by its date as the Python harness does. */
export function checkPositions(
  positions: Float64Array,
  timestamps: string[],
): { ok: true; positions: number[] } | { ok: false; message: string } {
  const date = (bar: number) => timestamps[bar]!.slice(0, 10)
  const missing = positions.reduce<number[]>((bars, value, bar) => {
    if (Number.isNaN(value)) bars.push(bar)
    return bars
  }, [])
  if (missing.length) {
    return {
      ok: false,
      message:
        `The position is NaN on ${missing.length} bars, the first on ${date(missing[0]!)}. ` +
        'Positions start at 0.0, so a NaN comes from arithmetic: on a missing open, high, ' +
        'low or volume (test with pyqxel::missing), or from 0.0 / 0.0.',
    }
  }
  const outside = positions.findIndex((value) => !(Math.abs(value) <= MAX_POSITION))
  if (outside >= 0) {
    return {
      ok: false,
      message:
        `Positions must be between -${MAX_POSITION} and ${MAX_POSITION}; ` +
        `${positions[outside]} on ${date(outside)} is not.`,
    }
  }
  return { ok: true, positions: Array.from(positions) }
}

/** Runs `module`'s strategy on `bars`, returning its checked positions and what it
 * printed, or a runtime or result error. */
export async function runStrategy(
  module: WebAssembly.Module,
  bars: BarColumns,
): Promise<WorkerOutcome> {
  let output = ''
  const decoder = new TextDecoder()
  const print = new ConsoleStdout((bytes) => {
    output += decoder.decode(bytes, { stream: true })
  })
  const wasi = new WASI([], [], [new OpenFile(new File([])), print, print])
  const instance = await WebAssembly.instantiate(module, {
    wasi_snapshot_preview1: wasi.wasiImport,
  })
  const strategy = instance.exports as unknown as StrategyModule
  const size = bars.timestamps.length
  const capped = () => output.slice(-MAX_OUTPUT)

  try {
    wasi.initialize(
      instance as { exports: { memory: WebAssembly.Memory; _initialize?: () => unknown } },
    )
    strategy.pyqxel_allocate(size)
    // Views are made after each call into the module, whose memory may have grown.
    COLUMNS.forEach((name, column) => {
      const view = new Float64Array(strategy.memory.buffer, strategy.pyqxel_column(column), size)
      view.set(bars[name].map((value) => value ?? Number.NaN))
    })
    new BigInt64Array(strategy.memory.buffer, strategy.pyqxel_timestamps(), size).set(
      bars.timestamps.map((stamp) => BigInt(Date.parse(stamp))),
    )
    strategy.pyqxel_run()
  } catch (error) {
    if (error instanceof WASIProcExit) {
      return failure(
        'runtime',
        `The strategy called exit(${error.code}). Return from strategy() instead.`,
        capped(),
      )
    }
    if (error instanceof WebAssembly.RuntimeError) {
      return failure(
        'runtime',
        `The strategy crashed: ${error.message}. Look for an index outside 0 to ` +
          'bars.size - 1, an integer division by zero, deep recursion or a call to abort().',
        capped(),
      )
    }
    throw error
  }

  const positions = new Float64Array(
    strategy.memory.buffer,
    strategy.pyqxel_column(POSITIONS),
    size,
  ).slice()
  const checked = checkPositions(positions, bars.timestamps)
  if (!checked.ok) return failure('result', checked.message, capped())
  return { ok: true, positions: checked.positions, output: capped() }
}
