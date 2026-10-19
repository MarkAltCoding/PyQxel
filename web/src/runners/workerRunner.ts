// Runs custom strategies in a Web Worker, one at a time. The worker is created on the
// first run (or `warm()`), so its runtime is only downloaded when someone uses it. A run
// that takes too long, or is cancelled, terminates the worker; the next run starts a
// fresh one, since a stuck interpreter cannot be interrupted any other way.
import type { CodeLanguage, OHLCVBar } from '../api/types'
import {
  barColumns,
  type BarColumns,
  type RunError,
  type RunOptions,
  type RunResult,
  RUNTIME_NAMES,
  type StrategyRunner,
} from './types'

export type WorkerRequest =
  { type: 'warm' } | { type: 'run'; id: number; code: string; bars: BarColumns }

export type WorkerOutcome =
  { ok: true; positions: number[]; output: string } | { ok: false; error: RunError; output: string }

export type WorkerResponse =
  /** The runtime is loaded and the user's code is starting; the run timeout starts now. */
  { type: 'started'; id: number } | { type: 'result'; id: number; outcome: WorkerOutcome }

export interface RunnerTimeouts {
  /** How long the user's code may run. */
  runMs: number
  /** How long the runtime may take to download and start, which is slow on phones. */
  loadMs: number
}

export const DEFAULT_TIMEOUTS: RunnerTimeouts = { runMs: 30_000, loadMs: 180_000 }

const failure = (kind: RunError['kind'], message: string): RunResult => ({
  ok: false,
  error: { kind, message, line: null, traceback: '' },
  output: '',
})

export class WorkerRunner implements StrategyRunner {
  readonly language: CodeLanguage
  private readonly create: () => Worker
  private readonly timeouts: RunnerTimeouts
  private worker: Worker | null = null
  private queue: Promise<unknown> = Promise.resolve()
  private nextId = 0

  constructor(language: CodeLanguage, create: () => Worker, timeouts = DEFAULT_TIMEOUTS) {
    this.language = language
    this.create = create
    this.timeouts = timeouts
  }

  warm(): void {
    this.ensure().postMessage({ type: 'warm' } satisfies WorkerRequest)
  }

  run(code: string, bars: OHLCVBar[], options: RunOptions = {}): Promise<RunResult> {
    const job = this.queue.then(() => this.execute(code, bars, options))
    this.queue = job
    return job
  }

  dispose(): void {
    this.worker?.terminate()
    this.worker = null
  }

  private ensure(): Worker {
    this.worker ??= this.create()
    return this.worker
  }

  private execute(code: string, bars: OHLCVBar[], options: RunOptions): Promise<RunResult> {
    const { signal, onStart } = options
    if (signal?.aborted) return Promise.resolve(failure('cancelled', 'The run was cancelled.'))
    const worker = this.ensure()
    const id = ++this.nextId
    const columns = barColumns(bars)
    const seconds = (ms: number) => Math.round(ms / 1000)

    return new Promise<RunResult>((resolve) => {
      const finish = (result: RunResult) => {
        clearTimeout(timer)
        worker.removeEventListener('message', onMessage)
        worker.removeEventListener('error', onError)
        signal?.removeEventListener('abort', onAbort)
        resolve(result)
      }
      const stop = (kind: RunError['kind'], message: string) => {
        if (this.worker === worker) this.dispose()
        else worker.terminate()
        finish(failure(kind, message))
      }
      const onMessage = (event: MessageEvent<WorkerResponse>) => {
        const message = event.data
        if (message.id !== id) return
        if (message.type === 'started') {
          clearTimeout(timer)
          onStart?.()
          timer = setTimeout(
            () =>
              stop(
                'timeout',
                `The strategy ran for more than ${seconds(this.timeouts.runMs)} seconds, so it ` +
                  'was stopped. Look for a loop that never ends, or vectorize a slow one.',
              ),
            this.timeouts.runMs,
          )
          return
        }
        const { outcome } = message
        if (outcome.ok && outcome.positions.length !== columns.timestamps.length) {
          finish(
            failure(
              'result',
              `The runner returned ${outcome.positions.length} positions for ` +
                `${columns.timestamps.length} bars.`,
            ),
          )
          return
        }
        finish(
          outcome.ok
            ? {
                ok: true,
                timestamps: columns.timestamps,
                positions: outcome.positions,
                output: outcome.output,
              }
            : outcome,
        )
      }
      const onError = (event: ErrorEvent) =>
        stop('load', event.message || `${RUNTIME_NAMES[this.language]} failed to start.`)
      const onAbort = () => stop('cancelled', 'The run was cancelled.')

      let timer = setTimeout(
        () =>
          stop(
            'load',
            `${RUNTIME_NAMES[this.language]} did not load within ` +
              `${seconds(this.timeouts.loadMs)} seconds. Check the connection and try again.`,
          ),
        this.timeouts.loadMs,
      )
      worker.addEventListener('message', onMessage)
      worker.addEventListener('error', onError)
      signal?.addEventListener('abort', onAbort)
      worker.postMessage({ type: 'run', id, code, bars: columns } satisfies WorkerRequest)
    })
  }
}
