// The interface every custom-strategy runner implements, whatever language it runs.
// A runner executes the user's code in the browser, never on the server, and returns
// one target position per bar for the server to score.
import type { CodeLanguage, OHLCVBar } from '../api/types'

/** What runs each language, as messages name it: "The C++ compiler could not start". */
export const RUNTIME_NAMES: Record<CodeLanguage, string> = {
  python: 'Python',
  cpp: 'The C++ compiler',
}

export interface RunError {
  /** `syntax` (Python) and `compile` (C++) are errors in the code before it runs,
   * `runtime` while it runs, `result` in what it returned, `timeout` when it ran too long,
   * `cancelled` when it was aborted, and `load` when the runtime failed to start. */
  kind: 'syntax' | 'compile' | 'runtime' | 'result' | 'timeout' | 'cancelled' | 'load'
  message: string
  /** The line of the user's code the error points at, when there is one. */
  line: number | null
  traceback: string
}

export type RunResult =
  | {
      ok: true
      /** The bars' timestamps exactly as the history endpoint sent them, with their UTC
       * offsets, which the server needs to match positions to bars. */
      timestamps: string[]
      positions: number[]
      /** What the code printed. */
      output: string
    }
  | { ok: false; error: RunError; output: string }

export interface RunOptions {
  /** Stops the run, which then resolves as `cancelled`. */
  signal?: AbortSignal
  /** Called once the runtime has loaded and the code starts running. */
  onStart?: () => void
}

export interface StrategyRunner {
  readonly language: CodeLanguage
  /** Runs `code` on `bars`, oldest first. Never rejects: failures are `ok: false`. */
  run(code: string, bars: OHLCVBar[], options?: RunOptions): Promise<RunResult>
  /** Starts loading the runtime ahead of the first run. */
  warm(): void
  /** Stops any run and frees the runtime. */
  dispose(): void
}

/** The bars as columns, the form the workers take them in. */
export interface BarColumns {
  timestamps: string[]
  open: (number | null)[]
  high: (number | null)[]
  low: (number | null)[]
  close: number[]
  volume: (number | null)[]
}

export function barColumns(bars: OHLCVBar[]): BarColumns {
  return {
    timestamps: bars.map((bar) => bar.timestamp),
    open: bars.map((bar) => bar.open ?? null),
    high: bars.map((bar) => bar.high ?? null),
    low: bars.map((bar) => bar.low ?? null),
    close: bars.map((bar) => bar.close),
    volume: bars.map((bar) => bar.volume ?? null),
  }
}
