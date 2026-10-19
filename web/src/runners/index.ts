// One runner per language for the whole session, so a loaded runtime is reused across
// runs and pages instead of downloaded again.
import type { CodeLanguage } from '../api/types'
import { createCppRunner } from './cpp'
import { createPythonRunner } from './python'
import type { RunError, StrategyRunner } from './types'

const runners = new Map<CodeLanguage, StrategyRunner>()

export function runnerFor(language: CodeLanguage): StrategyRunner {
  let runner = runners.get(language)
  if (!runner) {
    runner = language === 'cpp' ? createCppRunner() : createPythonRunner()
    runners.set(language, runner)
  }
  return runner
}

/** A strategy that failed in the browser, before anything reached the server. */
export class StrategyRunError extends Error {
  readonly failure: RunError
  readonly output: string

  constructor(failure: RunError, output: string) {
    super(failure.message)
    this.name = 'StrategyRunError'
    this.failure = failure
    this.output = output
  }
}
