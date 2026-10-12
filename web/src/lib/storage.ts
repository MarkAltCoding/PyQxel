// Browser storage that never throws: private windows and blocked site data make it
// unavailable, and the app must still work then.

export function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Not stored; the value lasts for this page load only.
  }
}
