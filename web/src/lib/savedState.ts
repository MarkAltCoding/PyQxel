// What a person enters or chooses in the app, saved to their account so it is there in
// every browser, on every device, and after every update or reinstall: strategy drafts,
// a screen's filters, the answers given for a review or for Claude's picks, and display
// choices. Holdings, backtests, reports, reviews, and picks are already kept by the
// server in tables of their own; this covers the rest, which once lived in one browser.
//
// The values stay in localStorage, where the pages read them at once, and are copied to
// the account: fetched when the app opens, before any page reads them, and sent a moment
// after each write. Keys and sign-ins never leave the browser (LOCAL_ONLY, and the server
// refuses them too).

import { useSyncExternalStore } from 'react'
import { getSavedState, putSavedState } from '../api/endpoints'
import { isSecure } from './secureStorage'
import { onStoredWrite, readStored, storedKeys, writeLocal } from './storage'

/** Names kept in this browser alone: secrets, sign-ins in progress, this device's own
 * state, the bulky data of an exported prompt, which is of use only where it was made,
 * and the acknowledgment of the advice notice, which each device shows once on its own. */
const LOCAL_ONLY = [
  'pyqxel.apiKey',
  'pyqxel.adviceNoticeAccepted',
  'pyqxel.providerKeys',
  'pyqxel.appSignIn',
  'pyqxel.signInStarted',
  'pyqxel.updateDismissed',
  'pyqxel.exportedPrompt.',
  'pyqxel.saved.',
]

/** The account the saved values in this browser belong to. */
const OWNER_KEY = 'pyqxel.saved.owner'
/** Names written here and not yet received by the account, as a JSON list. */
const PENDING_KEY = 'pyqxel.saved.pending'

/** How long after a write the account is sent it, so typing sends one request, not many. */
const SEND_AFTER_MS = 800

/** Whether `key` follows the account, as opposed to staying in this browser. */
export function follows(key: string): boolean {
  return (
    key.startsWith('pyqxel.') && !isSecure(key) && !LOCAL_ONLY.some((name) => key.startsWith(name))
  )
}

type Status = 'idle' | 'loading' | 'ready'

let status: Status = 'idle'
const watchers = new Set<() => void>()
let timer: ReturnType<typeof setTimeout> | null = null
let sending: Promise<void> = Promise.resolve()

function setStatus(next: Status): void {
  status = next
  for (const watcher of watchers) watcher()
}

function pending(): Set<string> {
  try {
    const names: unknown = JSON.parse(readStored(PENDING_KEY) ?? '[]')
    return new Set(Array.isArray(names) ? names.filter((name) => typeof name === 'string') : [])
  } catch {
    return new Set()
  }
}

function setPending(names: Set<string>): void {
  writeLocal(PENDING_KEY, names.size ? JSON.stringify([...names]) : null)
}

/** Sends the account every value written here that it has not yet received; with
 * `keepalive`, in a request that outlives the page, as when it is being left. */
async function send(keepalive = false): Promise<void> {
  const names = pending()
  if (names.size === 0) return
  const items: Record<string, string | null> = {}
  for (const name of names) items[name] = readStored(name)
  try {
    await putSavedState(items, keepalive)
  } catch {
    // Kept as pending, and sent with the next write or when the app next opens.
    return
  }
  // A value written again while this was in flight is still to be sent.
  const left = pending()
  for (const name of names) if (readStored(name) === items[name]) left.delete(name)
  setPending(left)
}

function sendSoon(): void {
  if (timer !== null) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    sending = sending.then(() => send())
  }, SEND_AFTER_MS)
}

function noteWrite(key: string): void {
  if (!follows(key)) return
  const names = pending()
  names.add(key)
  setPending(names)
  sendSoon()
}

/** Sends at once whatever is waiting, as when the page is being left. */
export function flushSavedState(keepalive = false): Promise<void> {
  if (timer !== null) {
    clearTimeout(timer)
    timer = null
  }
  sending = sending.then(() => send(keepalive))
  return sending
}

/**
 * Brings this browser in line with the account: takes what the account has saved, and
 * sends it what this browser has that the account lacks.
 *
 * The account's value wins, except for values written here that it never received
 * (pending), which are sent instead. A value saved here that the account no longer has was
 * removed elsewhere, so it is removed here too, unless this browser has never been
 * brought in line with this account, when everything it holds is sent up: that is how
 * what was entered before accounts kept it gets there. Values left by another account
 * are dropped first.
 */
export async function reconcile(account: string, saved: Record<string, string>): Promise<void> {
  const local = storedKeys().filter(follows)
  const owner = readStored(OWNER_KEY)
  if (owner !== null && owner !== account) {
    for (const key of local) writeLocal(key, null)
    setPending(new Set())
  }
  const firstTime = owner !== account
  const unsent = owner === account ? pending() : new Set<string>()
  for (const [key, value] of Object.entries(saved)) {
    if (follows(key) && !unsent.has(key)) writeLocal(key, value)
  }
  if (owner === null || owner === account) {
    for (const key of local) {
      if (key in saved || unsent.has(key)) continue
      if (firstTime) unsent.add(key)
      else writeLocal(key, null)
    }
  }
  writeLocal(OWNER_KEY, account)
  setPending(unsent)
  await send()
}

/**
 * Loads the account's saved values into this browser, once per sign-in, and from then on
 * sends the account each value written. Never throws: when the account cannot be
 * reached, the app goes on with what this browser holds, and what is written meanwhile
 * is sent once it can be.
 */
export async function startSavedState(): Promise<void> {
  if (status !== 'idle') return
  setStatus('loading')
  try {
    const state = await getSavedState()
    if (state && typeof state.account === 'string' && typeof state.items === 'object') {
      await reconcile(state.account, state.items ?? {})
    }
  } catch {
    // Offline, or a server without the route: this browser's own values are used.
  }
  onStoredWrite(noteWrite)
  setStatus('ready')
}

/**
 * Stops saving, as on signing out. With `forget`, what this browser holds of the account
 * is sent one last time and then removed from it, so the next person to use the browser
 * neither sees it nor has it saved to their account.
 */
export async function stopSavedState(forget: boolean): Promise<void> {
  if (forget) await flushSavedState().catch(() => {})
  if (timer !== null) clearTimeout(timer)
  timer = null
  onStoredWrite(null)
  if (forget) {
    for (const key of storedKeys().filter(follows)) writeLocal(key, null)
    writeLocal(OWNER_KEY, null)
    writeLocal(PENDING_KEY, null)
  }
  setStatus('idle')
}

function watch(watcher: () => void): () => void {
  watchers.add(watcher)
  return () => watchers.delete(watcher)
}

/** Whether the account's saved values are in this browser, so pages may read them. */
export function useSavedStateReady(): boolean {
  return useSyncExternalStore(watch, () => status === 'ready')
}

// Hiding or leaving the page sends what is waiting at once, in a request that outlives
// it, since the timer may never fire.
if (typeof window !== 'undefined') {
  const leave = () => {
    if (status === 'ready' && timer !== null) void flushSavedState(true)
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') leave()
  })
  window.addEventListener('pagehide', leave)
}
