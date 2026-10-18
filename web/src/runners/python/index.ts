import { WorkerRunner } from '../workerRunner'
import type { StrategyRunner } from '../types'

/** A runner for Python strategies. Pyodide is only downloaded on its first run or `warm()`. */
export function createPythonRunner(): StrategyRunner {
  return new WorkerRunner(
    'python',
    () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'python' }),
  )
}
