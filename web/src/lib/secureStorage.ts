// Secure storage for the native app's secrets: the PyQxel API key it is signed in with
// (`pyqxel.apiKey`) and the provider keys (`pyqxel.providerKeys`). In the app they are kept
// by the native side (`src-tauri/src/secrets.rs`): the Keychain on iOS, a file only the
// person's account can read in the app's sandboxed container on macOS. In a browser they
// stay in local storage, as before.
//
// They are read once before the app first renders (`loadSecureStorage`), so the rest of
// the app reads them synchronously through `readStored`, and each change is written
// through to the native side at once, which has it on disk before it answers. Web storage
// is not used for them in the app, because WKWebView writes it to disk a moment after a
// change, and a change made just before iOS killed the app was lost.
//
// Threat model
// ------------
// What the keys are worth: the PyQxel API key reads and changes the person's account (its
// portfolios, saved research, settings) for up to 30 days, renewed while the app is used;
// the provider keys spend the person's plans with Anthropic and the market data services.
//
// - Script injected into the page (XSS): the worst case, since script on the page can do
//   anything the page can, including ask the native side for the keys. The defense is
//   keeping script out: the app runs under a Content Security Policy as strict as the
//   website's (`tauri.conf.json`), so only the app's own bundled scripts run, with no
//   inline script and no eval, and requests go only to PyQxel's server, so a script that
//   did get in could not send the keys anywhere else. React escapes what it renders, AI
//   reports are rendered as text, and custom strategy code runs in a Web Worker that is
//   cut off from the network and from the page. The native side keeps two names only.
// - Other apps on the device: on iOS, the Keychain item belongs to this app alone and
//   opens only after the phone's first unlock since it started; on macOS, the App Sandbox
//   keeps other sandboxed apps out of the container, and macOS asks before an unsandboxed
//   app reads another app's container. Neither guards against malware already running as
//   the person on a Mac, which could read the file, as it could the browser's storage.
//   The keys never travel in a link: the sign-in link carries a one-time code that is
//   useless without a secret the app kept (see `auth/nativeSignIn.ts`).
// - A lost or stolen device: on iOS, the Keychain item is encrypted with the device's
//   passcode and stays on the device, never in iCloud or in a backup restored to another
//   phone, so a locked phone does not give it up. On a Mac, FileVault protects it while
//   the Mac is off or locked. Either way, signing in from another device and revoking the
//   key in Settings (or deleting the account) cuts it off, and the key expires 30 days
//   after the app was last used.
// - A shared device: anyone who can use the unlocked device and open the app is signed in
//   as its owner. Signing out revokes the API key on the server and deletes both the key
//   and the provider keys from the device. Deleting the app deletes them too: on iOS, the
//   first launch of a new installation clears what the Keychain kept from an earlier one.
//
// Not covered: a device that is jailbroken or already compromised, and someone who knows
// the device's passcode or the person's account password.

import { isNativeApp } from './native'

/** The names kept in secure storage in the app; everything else stays in web storage. */
export const SECURE_NAMES: readonly string[] = ['pyqxel.apiKey', 'pyqxel.providerKeys']

/** The secrets once loaded in the app; `null` in a browser, or before they are loaded. */
let loaded: Map<string, string> | null = null

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>

async function nativeInvoke(): Promise<Invoke> {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke
}

function readWebStorage(name: string): string | null {
  try {
    return window.localStorage.getItem(name)
  } catch {
    return null
  }
}

function removeWebStorage(name: string): void {
  try {
    window.localStorage.removeItem(name)
  } catch {
    // Nothing to remove.
  }
}

/**
 * Reads the secrets from the native side before the app first renders, and moves any
 * that an earlier version of the app left in web storage there. Does nothing in a
 * browser. If the native side cannot be reached, the app keeps using web storage.
 */
export async function loadSecureStorage(invoke?: Invoke): Promise<void> {
  if (!invoke && !isNativeApp()) return
  try {
    const call = invoke ?? (await nativeInvoke())
    const secrets = new Map<string, string>()
    for (const name of SECURE_NAMES) {
      let value = await call<string | null>('secret_get', { name })
      const left = readWebStorage(name)
      if (value === null && left !== null) {
        await call('secret_set', { name, value: left })
        value = left
      }
      if (left !== null) removeWebStorage(name)
      if (value !== null) secrets.set(name, value)
    }
    loaded = secrets
    writer = call
  } catch {
    loaded = null
  }
}

let writer: Invoke | null = null

/** Whether `name` is kept in secure storage now; reads and writes of it go here. */
export function isSecure(name: string): boolean {
  return loaded !== null && SECURE_NAMES.includes(name)
}

/** The secret kept under `name`, from what was loaded. */
export function readSecure(name: string): string | null {
  return loaded?.get(name) ?? null
}

/**
 * Keeps `value` under `name`, or forgets it when `null`: in memory at once, and on the
 * native side, which writes it to disk before answering. If that fails, it is kept in
 * web storage instead, so it is not lost.
 */
export function writeSecure(name: string, value: string | null): Promise<void> {
  if (value === null) loaded?.delete(name)
  else loaded?.set(name, value)
  const call = writer
  if (!call) return Promise.resolve()
  const written =
    value === null ? call('secret_delete', { name }) : call('secret_set', { name, value })
  return written.then(
    () => undefined,
    () => {
      try {
        if (value === null) window.localStorage.removeItem(name)
        else window.localStorage.setItem(name, value)
      } catch {
        // Kept for this run of the app only.
      }
    },
  )
}

/** Forgets what was loaded; for tests. */
export function resetSecureStorage(): void {
  loaded = null
  writer = null
}
