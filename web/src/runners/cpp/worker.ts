// The Web Worker that compiles custom C++ strategies to WebAssembly with Clang and runs
// them on a WASI shim.
//
// Clang is loaded from the app's own copy (scripts/vendor-clang.mjs), never a CDN, at
// VITE_CLANG_URL or /clang/. Once it has loaded, the worker loses its network APIs, so
// neither the compiler nor the strategy can send data anywhere or fetch more code.
import type { Command } from '@yowasp/clang'
import { cutNetwork } from '../lockdown'
import type { WorkerRequest, WorkerResponse } from '../workerRunner'
import { compileStrategy } from './compile'
import { createCppHandler } from './handler'
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

const handle = createCppHandler({
  async loadClang() {
    const { commands } = (await import(/* @vite-ignore */ `${BASE}bundle.js`)) as {
      commands: Record<'clang++', Command>
    }
    const clang = commands['clang++']
    // The first run fetches the compiler and its headers and libraries, all of them,
    // before the network goes.
    await clang(['--version'], {}, { stdout: () => {}, stderr: () => {}, fetchProgress: () => {} })
    cutNetwork(scope)
    return clang
  },
  compile: compileStrategy,
  instantiate: (wasm) => WebAssembly.compile(wasm as BufferSource),
  run: runStrategy,
  post: (message) => scope.postMessage(message),
})

scope.onmessage = (event) => {
  void handle(event.data)
}
