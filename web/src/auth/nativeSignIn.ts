// Signing in to the native app (Tauri) with GitHub, Google or Apple. Providers refuse
// sign-ins inside an app's own web view, so the app opens the sign-in in the system
// browser, which comes back through a `pyqxel://signin#code=…` link that opens the app.
//
// Any app could register `pyqxel://` and see that link, so the code in it is not enough
// to sign in: the app also keeps a random verifier, sends only its SHA-256 challenge when
// the sign-in starts, and trades the code and the verifier together for its API key. A
// link that arrives while no sign-in is waiting is ignored, so nobody can sign the app
// into their own account by sending a link.

import { apiUrl } from '../api/client'

const STORAGE_KEY = 'pyqxel.appSignIn'

/** How long a sign-in started in the browser may take to come back. */
const MAX_AGE_MS = 15 * 60 * 1000

/** The link the server opens the app with when a sign-in finishes. */
export const SIGN_IN_LINK = 'pyqxel://signin'

export { isNativeApp } from '../lib/native'

function base64Url(bytes: Uint8Array): string {
  let text = ''
  for (const byte of bytes) text += String.fromCharCode(byte)
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** A new verifier and its S256 challenge. */
export async function newVerifier(): Promise<{ verifier: string; challenge: string }> {
  const verifier = base64Url(crypto.getRandomValues(new Uint8Array(32)))
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))
  return { verifier, challenge: base64Url(new Uint8Array(digest)) }
}

/**
 * Opens the sign-in with `provider` in the system browser, to come back to `next`.
 * Kept in local storage rather than session storage, since a phone may close the app
 * while the browser is in front.
 */
export async function startNativeSignIn(provider: string, next: string): Promise<void> {
  const { verifier, challenge } = await newVerifier()
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ verifier, started: Date.now() }))
  } catch {
    // Without storage the sign-in cannot be finished when it comes back.
  }
  const { openUrl } = await import('@tauri-apps/plugin-opener')
  const query = new URLSearchParams({ next, app: challenge })
  await openUrl(apiUrl(`/api/v1/auth/${encodeURIComponent(provider)}/start?${query}`))
}

/** The verifier of a sign-in started in the last 15 minutes, or `null`. Only reads. */
export function pendingVerifier(now: number = Date.now()): string | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const pending = JSON.parse(raw) as { verifier?: unknown; started?: unknown }
    const age = now - Number(pending.started)
    return typeof pending.verifier === 'string' && age >= 0 && age <= MAX_AGE_MS
      ? pending.verifier
      : null
  } catch {
    return null
  }
}

/** Uses up the waiting sign-in, once what came back has been handled. */
export function forgetNativeSignIn(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing to forget.
  }
}

/** The fragment of a `pyqxel://signin#…` link, or `null` for any other link. */
export function signInLinkFragment(url: string): string | null {
  if (!url.startsWith(SIGN_IN_LINK)) return null
  const rest = url.slice(SIGN_IN_LINK.length).replace(/^\/+/, '')
  if (rest !== '' && !rest.startsWith('#')) return null
  return rest.replace(/^#/, '')
}

/**
 * Calls `onFragment` with the fragment of each sign-in link that opens the app: the one
 * it was launched with, if any, and those that arrive while it runs. Returns a function
 * that stops listening.
 */
export function listenForSignInLinks(onFragment: (fragment: string) => void): () => void {
  let stop: (() => void) | null = null
  let stopped = false
  const handle = (urls: string[] | null) => {
    for (const url of urls ?? []) {
      const fragment = signInLinkFragment(url)
      if (fragment !== null) onFragment(fragment)
    }
  }
  void import('@tauri-apps/plugin-deep-link').then(async ({ getCurrent, onOpenUrl }) => {
    const unlisten = await onOpenUrl(handle)
    if (stopped) unlisten()
    else stop = unlisten
    handle(await getCurrent())
  })
  return () => {
    stopped = true
    stop?.()
  }
}
