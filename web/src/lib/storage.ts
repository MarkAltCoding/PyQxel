// Browser storage that never throws: private windows and blocked site data make it
// unavailable, and the app must still work then. In the native app, the sign-in key and
// provider keys go to secure storage instead (`secureStorage.ts`).

import { isSecure, readSecure, writeSecure } from './secureStorage'

export function readStored(key: string): string | null {
  if (isSecure(key)) return readSecure(key)
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string | null): void {
  if (isSecure(key)) {
    void writeSecure(key, value)
    return
  }
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Not stored; the value lasts for this page load only.
  }
}
