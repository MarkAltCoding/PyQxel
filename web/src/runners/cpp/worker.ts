// The Web Worker that compiles custom C++ strategies to WebAssembly with Clang and runs
// them on a WASI shim.
//
// Clang is loaded from the app's own copy (scripts/vendor-clang.mjs), never a CDN, at
// VITE_CLANG_URL or /clang/. Once it has loaded, the worker loses its network APIs, so
// neither the compiler nor the strategy can send data anywhere or fetch more code.
import type { Command } from '@yowasp/clang'
import { cutNetwork } from '../lockdown'
import type { WorkerOutcome, WorkerRequest, WorkerResponse } from '../workerRunner'
import { compileStrategy } from './compile'
import { runStrategy } from './execute'

/** The worker's global scope; the app is typed against the DOM, not WebWorker, library. */
const scope = self as unknown as {
  location: Location
  postMessage(message: WorkerResponse): void
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null
}

const BASE = new URL(
  import.meta.env.VITE_CLANG_URL || `${import.meta.env.BASE_URL}clang/`,
  scope.location.href,
).href

/** Compiled strategies and their warnings by source, so rerunning unchanged code skips
 * the compiler. */
const compiled = new Map<string, { module: WebAssembly.Module; warnings: string }>()
const COMPILED_KEPT = 4

let ready: Promise<Command> | null = null

function load(): Promise<Command> {
  ready ??= (async () => {
    const { commands } = (await import(/* @vite-ignore */ `${BASE}bundle.js`)) as {
      commands: Record<'clang++', Command>
    }
    const clang = commands['clang++']
    // The first run fetches the compiler and its headers and libraries, all of them,
    // before the network goes.
    await clang(['--version'], {}, { stdout: () => {}, stderr: () => {}, fetchProgress: () => {} })
    cutNetwork(scope)
    return clang
  })()
  ready.catch(() => {
    ready = null
  })
  return ready
}

const failed = (kind: 'load' | 'runtime', message: string): WorkerOutcome => ({
  ok: false,
  error: { kind, message, line: null, traceback: '' },
  output: '',
})

async function build(
  clang: Command,
  code: string,
): Promise<{ module: WebAssembly.Module; warnings: string } | WorkerOutcome> {
  const known = compiled.get(code)
  if (known) return known
  const result = await compileStrategy(clang, code)
  if (!result.ok) return { ok: false, error: result.error, output: '' }
  const built = {
    module: await WebAssembly.compile(result.wasm as BufferSource),
    warnings: result.warnings,
  }
  compiled.set(code, built)
  while (compiled.size > COMPILED_KEPT) compiled.delete(compiled.keys().next().value!)
  return built
}

scope.onmessage = async (event) => {
  const request = event.data
  if (request.type === 'warm') {
    load().catch(() => {})
    return
  }
  let clang: Command
  try {
    clang = await load()
  } catch (error) {
    const outcome = failed('load', `The C++ compiler could not start: ${String(error)}`)
    scope.postMessage({ type: 'result', id: request.id, outcome })
    return
  }
  // The run's time limit covers compiling too: templates and constexpr run in the compiler.
  scope.postMessage({ type: 'started', id: request.id })
  let outcome: WorkerOutcome
  try {
    const built = await build(clang, request.code)
    if ('module' in built) {
      outcome = await runStrategy(built.module, request.bars)
      // Warnings come first, as they would in a terminal.
      if (built.warnings) outcome.output = `${built.warnings}\n\n${outcome.output}`
    } else {
      outcome = built
    }
  } catch (error) {
    outcome = failed('runtime', String(error))
  }
  scope.postMessage({ type: 'result', id: request.id, outcome })
}
