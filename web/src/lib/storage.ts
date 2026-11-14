// Browser storage that never throws: private windows and blocked site data make it
// unavailable, and the app must still work then. In the native app, the sign-in key and
// provider keys go to secure storage instead (`secureStorage.ts`). Everything else a
// person enters or chooses is also saved to their account (`savedState.ts`), which hears
// of each write through `onStoredWrite`.

import { isSecure, readSecure, writeSecure } from './secureStorage'

type WriteListener = (key: string, value: string | null) => void

let listener: WriteListener | null = null

/** Has `heard` told of every value written outside secure storage, or stops with null. */
export function onStoredWrite(heard: WriteListener | null): void {
  listener = heard
}

export function readStored(key: string): string | null {
  if (isSecure(key)) return readSecure(key)
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

/** Writes to this browser alone, telling no listener: for what the account sent here. */
export function writeLocal(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Not stored; the value lasts for this page load only.
  }
}

export function writeStored(key: string, value: string | null): void {
  if (isSecure(key)) {
    void writeSecure(key, value)
    return
  }
  writeLocal(key, value)
  listener?.(key, value)
}

/** The names of everything stored in this browser, or none when storage is unavailable. */
export function storedKeys(): string[] {
  try {
    return Object.keys(window.localStorage)
  } catch {
    return []
  }
}
