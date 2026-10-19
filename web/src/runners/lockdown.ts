// Takes a strategy worker off the network once its runtime has loaded, so the user's
// code cannot send data anywhere or fetch more code. The worker's Content-Security-Policy
// (app/web.py) bounds what is left, such as eval.

/** Globals that reach the network or load code. */
const NETWORK = [
  'fetch',
  'XMLHttpRequest',
  'WebSocket',
  'EventSource',
  'WebTransport',
  'importScripts',
  'Worker',
  'SharedWorker',
]

/** Removes every network global, from the scope and the prototypes it inherits them from. */
export function cutNetwork(scope: object = globalThis): void {
  for (const name of NETWORK) {
    for (let target: object | null = scope; target; target = Object.getPrototypeOf(target)) {
      if (Object.prototype.hasOwnProperty.call(target, name)) Reflect.deleteProperty(target, name)
    }
    Object.defineProperty(scope, name, { value: undefined, writable: false, configurable: false })
  }
}
