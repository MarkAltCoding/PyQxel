// Runs a compiled C++ strategy on the bars and checks the positions it set, on the WASI
// shim with no files, no network and an empty stdin: only stdout and stderr reach out.
import { ConsoleStdout, File, OpenFile, WASI, WASIProcExit } from '@bjorn3/browser_wasi_shim'
import type { BarColumns, UniverseColumns } from '../types'
import type { PositionsOutcome, WeightsOutcome } from '../workerRunner'

/** Largest position, long or short, as a multiple of capital; the server checks it too. */
export const MAX_POSITION = 2
/** Most characters of printed output passed back. */
export const MAX_OUTPUT = 20_000

/** Largest sum of absolute weights across symbols on a bar; the server checks it too. */
export const MAX_GROSS = 2

/** What the entry point (entry.cpp) exports. */
interface StrategyModule {
  memory: WebAssembly.Memory
  _initialize: () => void
  pyqxel_allocate: (size: number) => void
  pyqxel_column: (column: number) => number
  pyqxel_timestamps: () => number
  pyqxel_run: () => void
}

/** What the entry point exports when compiled with PYQXEL_UNIVERSE. */
interface UniverseModule {
  memory: WebAssembly.Memory
  pyqxel_allocate: (size: number, count: number, nameBytes: number) => void
  pyqxel_column: (asset: number, field: number) => number
  pyqxel_symbols: () => number
  pyqxel_weights: () => number
  pyqxel_timestamps: () => number
  pyqxel_run: () => void
}

/** The column numbers entry.cpp gives open, high, low, close, volume and positions. */
const COLUMNS = ['open', 'high', 'low', 'close', 'volume'] as const
const POSITIONS = 5

const failure = (
  kind: 'runtime' | 'result',
  message: string,
  output: string,
): {
  ok: false
  error: { kind: 'runtime' | 'result'; message: string; line: null; traceback: string }
  output: string
} => ({
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

/** The weights as rows of numbers, or why they cannot be backtested, naming the first
 * bad bar by its date and symbol. */
export function checkWeights(
  weights: Float64Array,
  universe: UniverseColumns,
): { ok: true; weights: number[][] } | { ok: false; message: string } {
  const count = universe.symbols.length
  const where = (cell: number) =>
    `${universe.symbols[cell % count]} on ${universe.dates[Math.floor(cell / count)]}`
  const missing = weights.findIndex((value) => Number.isNaN(value))
  if (missing >= 0) {
    return {
      ok: false,
      message:
        `The weight of ${where(missing)} is NaN. Weights start at 0.0, so a NaN comes from ` +
        'arithmetic: on a missing open, high, low or volume (test with pyqxel::missing), or ' +
        'from 0.0 / 0.0.',
    }
  }
  const outside = weights.findIndex((value) => !(Math.abs(value) <= MAX_POSITION))
  if (outside >= 0) {
    return {
      ok: false,
      message:
        `Weights must be between -${MAX_POSITION} and ${MAX_POSITION}; ` +
        `${weights[outside]} for ${where(outside)} is not.`,
    }
  }
  const rows = universe.dates.map((_, bar) =>
    Array.from(weights.subarray(bar * count, (bar + 1) * count)),
  )
  const over = rows.findIndex(
    (row) => row.reduce((sum, value) => sum + Math.abs(value), 0) > MAX_GROSS + 1e-9,
  )
  if (over >= 0) {
    return {
      ok: false,
      message:
        `The absolute weights may add up to at most ${MAX_GROSS} on a bar; on ` +
        `${universe.dates[over]} they add up to ` +
        `${rows[over]!.reduce((sum, value) => sum + Math.abs(value), 0)}.`,
    }
  }
  return { ok: true, weights: rows }
}

/** Instantiates `module` with stdout and stderr collected, and runs `call` in it,
 * turning exit() and traps into runtime errors. */
async function execute<Exports>(
  module: WebAssembly.Module,
  call: (exports: Exports) => void,
): Promise<{ ok: true; exports: Exports; output: string } | ReturnType<typeof failure>> {
  let output = ''
  const decoder = new TextDecoder()
  const print = new ConsoleStdout((bytes) => {
    output += decoder.decode(bytes, { stream: true })
  })
  const wasi = new WASI([], [], [new OpenFile(new File([])), print, print])
  const instance = await WebAssembly.instantiate(module, {
    wasi_snapshot_preview1: wasi.wasiImport,
  })
  const exports = instance.exports as unknown as Exports
  const capped = () => output.slice(-MAX_OUTPUT)
  try {
    wasi.initialize(
      instance as { exports: { memory: WebAssembly.Memory; _initialize?: () => unknown } },
    )
    call(exports)
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
  return { ok: true, exports, output: capped() }
}

/** Runs `module`'s strategy on `bars`, returning its checked positions and what it
 * printed, or a runtime or result error. */
export async function runStrategy(
  module: WebAssembly.Module,
  bars: BarColumns,
): Promise<PositionsOutcome> {
  const size = bars.timestamps.length
  const ran = await execute<StrategyModule>(module, (strategy) => {
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
  })
  if (!ran.ok) return ran
  const { exports: strategy, output } = ran

  const positions = new Float64Array(
    strategy.memory.buffer,
    strategy.pyqxel_column(POSITIONS),
    size,
  ).slice()
  const checked = checkPositions(positions, bars.timestamps)
  if (!checked.ok) return failure('result', checked.message, output)
  return { ok: true, positions: checked.positions, output }
}

/** Runs `module`'s strategy, compiled with PYQXEL_UNIVERSE, across `universe`'s symbols,
 * returning its checked weights and what it printed, or a runtime or result error. */
export async function runUniverseStrategy(
  module: WebAssembly.Module,
  universe: UniverseColumns,
): Promise<WeightsOutcome> {
  const size = universe.dates.length
  const count = universe.symbols.length
  const names = new TextEncoder().encode(universe.symbols.map((symbol) => `${symbol}\0`).join(''))
  const ran = await execute<UniverseModule>(module, (strategy) => {
    strategy.pyqxel_allocate(size, count, names.length)
    // Views are made after each call into the module, whose memory may have grown.
    universe.assets.forEach((asset, index) => {
      COLUMNS.forEach((name, field) => {
        const view = new Float64Array(
          strategy.memory.buffer,
          strategy.pyqxel_column(index, field),
          size,
        )
        view.set(asset[name].map((value) => value ?? Number.NaN))
      })
    })
    new Uint8Array(strategy.memory.buffer, strategy.pyqxel_symbols(), names.length).set(names)
    new BigInt64Array(strategy.memory.buffer, strategy.pyqxel_timestamps(), size).set(
      universe.dates.map((date) => BigInt(Date.parse(`${date}T00:00:00Z`))),
    )
    strategy.pyqxel_run()
  })
  if (!ran.ok) return ran
  const weights = new Float64Array(
    ran.exports.memory.buffer,
    ran.exports.pyqxel_weights(),
    size * count,
  ).slice()
  const checked = checkWeights(weights, universe)
  if (!checked.ok) return failure('result', checked.message, ran.output)
  return { ok: true, weights: checked.weights, output: ran.output }
}
