// Whether the page runs in the native app (Tauri) rather than a browser. In its own
// module, with no imports, so storage and the API client can ask without a cycle.

/** Whether this page runs inside the native app rather than a browser. */
export function isNativeApp(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}
