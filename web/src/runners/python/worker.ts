// The Web Worker that runs custom Python strategies on Pyodide, NumPy and pandas.
//
// Pyodide is loaded from the app's own copy (scripts/vendor-pyodide.mjs), never a CDN,
// at VITE_PYODIDE_URL or /pyodide/. Once the packages are in, the worker loses its
// network APIs, so strategy code cannot send data anywhere or fetch more code through
// them. It runs the user's code only in this worker, never on the main thread or server.
import type { loadPyodide as LoadPyodide } from 'pyodide'
import harness from './harness.py?raw'
import { cutNetwork } from '../lockdown'
import type { WorkerOutcome, WorkerRequest, WorkerResponse } from '../workerRunner'

/** The worker's global scope; the app is typed against the DOM, not WebWorker, library. */
const scope = self as unknown as {
  location: Location
  postMessage(message: WorkerResponse): void
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null
}

const BASE = new URL(
  import.meta.env.VITE_PYODIDE_URL || `${import.meta.env.BASE_URL}pyodide/`,
  scope.location.href,
).href

type RunStrategy = (code: string, payload: string) => string

let ready: Promise<RunStrategy> | null = null

function load(): Promise<RunStrategy> {
  ready ??= (async () => {
    const { loadPyodide } = (await import(/* @vite-ignore */ `${BASE}pyodide.mjs`)) as {
      loadPyodide: typeof LoadPyodide
    }
    const pyodide = await loadPyodide({ indexURL: BASE })
    await pyodide.loadPackage(['numpy', 'pandas'], {
      checkIntegrity: true,
      messageCallback: () => {},
    })
    pyodide.runPython(harness)
    const run = pyodide.globals.get('run_strategy') as RunStrategy
    cutNetwork(scope)
    return run
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

scope.onmessage = async (event) => {
  const request = event.data
  if (request.type === 'warm') {
    load().catch(() => {})
    return
  }
  let run: RunStrategy
  try {
    run = await load()
  } catch (error) {
    const outcome = failed('load', `Python could not start: ${String(error)}`)
    scope.postMessage({ type: 'result', id: request.id, outcome })
    return
  }
  scope.postMessage({ type: 'started', id: request.id })
  let outcome: WorkerOutcome
  try {
    outcome = JSON.parse(run(request.code, JSON.stringify(request.bars))) as WorkerOutcome
  } catch (error) {
    outcome = failed('runtime', String(error))
  }
  scope.postMessage({ type: 'result', id: request.id, outcome })
}
