import { WorkerRunner } from '../workerRunner'
import type { StrategyRunner } from '../types'

/** A runner for C++ strategies. Clang is only downloaded on its first run or `warm()`. */
export function createCppRunner(): StrategyRunner {
  return new WorkerRunner(
    'cpp',
    () => new Worker(new URL('./worker.ts', import.meta.url), { type: 'module', name: 'cpp' }),
  )
}
