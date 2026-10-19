// Compiles a custom C++ strategy to a WebAssembly module with YoWASP Clang. The compiler
// is passed in: the worker imports the app's vendored copy, and tests the npm package.
import type { Command, Tree } from '@yowasp/clang'
import type { RunError } from '../types'
import entry from './entry.cpp?raw'
import header from './pyqxel.h?raw'

/** The name the user's code is compiled under, which marks its lines in diagnostics. */
export const SOURCE = 'strategy.cpp'

/** C++20 with the optimizer on and exceptions off, as a WASI reactor whose functions the
 * runner calls. The stack gets 1 MiB for recursion, and memory is capped at 512 MiB so a
 * runaway allocation fails before a phone runs out. */
export const FLAGS = [
  '-std=c++20',
  '-O2',
  '-fno-exceptions',
  '-mexec-model=reactor',
  '-I.',
  '-include',
  'pyqxel.h',
  '-Wl,-z,stack-size=1048576',
  '-Wl,--max-memory=536870912',
]

/** Most characters of compiler output passed back. */
export const MAX_DIAGNOSTICS = 20_000

const SIGNATURE = 'void strategy(const pyqxel::Bars& bars, std::span<double> positions)'

export type CompileResult =
  { ok: true; wasm: Uint8Array; warnings: string } | { ok: false; error: RunError }

/** The first error in Clang's output: its line in the user's code, when it is there. */
export function compileError(diagnostics: string): RunError {
  const traceback = diagnostics.trim().slice(0, MAX_DIAGNOSTICS)
  const own = new RegExp(`^${SOURCE.replace('.', '\\.')}:(\\d+):\\d+: (?:fatal )?error: (.*)$`, 'm')
  const located = own.exec(diagnostics)
  if (located) {
    return { kind: 'compile', message: located[2]!, line: Number(located[1]), traceback }
  }
  if (/undefined symbol: strategy\(/.test(diagnostics)) {
    return {
      kind: 'compile',
      message: `Define ${SIGNATURE}.`,
      line: null,
      traceback,
    }
  }
  const anyError = /^.*error: (.*)$/m.exec(diagnostics)
  return {
    kind: 'compile',
    message: anyError?.[1] ?? 'The strategy did not compile.',
    line: null,
    traceback,
  }
}

const isExit = (error: unknown): boolean =>
  error instanceof Error && typeof (error as { code?: unknown }).code === 'number'

/** Compiles `code`'s `strategy` with the entry point that calls it.
 *
 * Returns the module's bytes and any warnings, or the first error as a `compile`
 * RunError. Throws only when the compiler itself fails, not the code. */
export async function compileStrategy(clang: Command, code: string): Promise<CompileResult> {
  let diagnostics = ''
  const decoder = new TextDecoder()
  const collect = (bytes: Uint8Array | null) => {
    if (bytes) diagnostics += decoder.decode(bytes, { stream: true })
  }
  const files: Tree = { [SOURCE]: code, 'pyqxel.h': header, 'pyqxel_entry.cpp': entry }
  let output: Tree | undefined
  try {
    output = await clang([...FLAGS, SOURCE, 'pyqxel_entry.cpp', '-o', 'strategy.wasm'], files, {
      stdout: collect,
      stderr: collect,
      fetchProgress: () => {},
    })
  } catch (error) {
    if (isExit(error)) return { ok: false, error: compileError(diagnostics) }
    throw error
  }
  const wasm = output?.['strategy.wasm']
  if (!(wasm instanceof Uint8Array)) return { ok: false, error: compileError(diagnostics) }
  return { ok: true, wasm, warnings: diagnostics.trim().slice(0, MAX_DIAGNOSTICS) }
}
