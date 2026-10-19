// What the C++ worker does with each message, apart from the worker itself so that it can
// be tested with its compiler, module compiler and runner replaced.
import type { Command } from '@yowasp/clang'
import type { BarColumns } from '../types'
import type { WorkerOutcome, WorkerRequest, WorkerResponse } from '../workerRunner'
import type { CompileResult } from './compile'

export interface CppWorkerParts {
  /** Loads Clang, fetching everything it needs, and takes the worker off the network. */
  loadClang: () => Promise<Command>
  compile: (clang: Command, code: string) => Promise<CompileResult>
  instantiate: (wasm: Uint8Array) => Promise<WebAssembly.Module>
  run: (module: WebAssembly.Module, bars: BarColumns) => Promise<WorkerOutcome>
  post: (message: WorkerResponse) => void
}

/** Compiled strategies kept, so rerunning recent code skips the compiler. */
export const COMPILED_KEPT = 4

const failed = (kind: 'load' | 'runtime', message: string): WorkerOutcome => ({
  ok: false,
  error: { kind, message, line: null, traceback: '' },
  output: '',
})

/** The worker's message handler. Clang is loaded once, on the first message, and loaded
 * again if that failed; compiled modules are kept by source, with their warnings. */
export function createCppHandler(parts: CppWorkerParts): (request: WorkerRequest) => Promise<void> {
  const compiled = new Map<string, { module: WebAssembly.Module; warnings: string }>()
  let ready: Promise<Command> | null = null

  const load = (): Promise<Command> => {
    ready ??= parts.loadClang()
    ready.catch(() => {
      ready = null
    })
    return ready
  }

  const build = async (
    clang: Command,
    code: string,
  ): Promise<{ module: WebAssembly.Module; warnings: string } | WorkerOutcome> => {
    const known = compiled.get(code)
    if (known) {
      // Most recently used last, so the oldest is dropped first.
      compiled.delete(code)
      compiled.set(code, known)
      return known
    }
    const result = await parts.compile(clang, code)
    if (!result.ok) return { ok: false, error: result.error, output: '' }
    const built = { module: await parts.instantiate(result.wasm), warnings: result.warnings }
    compiled.set(code, built)
    while (compiled.size > COMPILED_KEPT) compiled.delete(compiled.keys().next().value!)
    return built
  }

  return async (request) => {
    if (request.type === 'warm') {
      await load().catch(() => {})
      return
    }
    let clang: Command
    try {
      clang = await load()
    } catch (error) {
      const outcome = failed('load', `The C++ compiler could not start: ${String(error)}`)
      parts.post({ type: 'result', id: request.id, outcome })
      return
    }
    // The run's time limit covers compiling too: templates and constexpr run in the compiler.
    parts.post({ type: 'started', id: request.id })
    let outcome: WorkerOutcome
    try {
      const built = await build(clang, request.code)
      if ('module' in built) {
        outcome = await parts.run(built.module, request.bars)
        // Warnings come first, as they would in a terminal.
        if (built.warnings)
          outcome = { ...outcome, output: `${built.warnings}\n\n${outcome.output}` }
      } else {
        outcome = built
      }
    } catch (error) {
      outcome = failed('runtime', String(error))
    }
    parts.post({ type: 'result', id: request.id, outcome })
  }
}
