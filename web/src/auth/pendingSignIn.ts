// Marks a GitHub or Google sign-in as started in this tab. A sign-in comes back with its
// API key in the URL fragment, so anyone could send a link carrying their own key and
// sign a visitor into the sender's account, where what the visitor saves would be the
// sender's to read; or carrying an error message of their own. The fragment is only
// trusted when this tab started a sign-in moments before. Session storage is per tab and
// survives the trip to the provider and back.

const STORAGE_KEY = 'pyqxel.signInStarted'

/** How long a started sign-in may take to come back. */
const MAX_AGE_MS = 15 * 60 * 1000

export function markSignInStarted(): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, String(Date.now()))
  } catch {
    // Without session storage the sign-in cannot be confirmed when it comes back.
  }
}

/** Whether this tab started a sign-in recently. Only reads, so it can run while rendering. */
export function signInStartedRecently(now: number = Date.now()): boolean {
  try {
    const started = Number(window.sessionStorage.getItem(STORAGE_KEY))
    return started > 0 && now - started >= 0 && now - started <= MAX_AGE_MS
  } catch {
    return false
  }
}

/** Uses up the mark, once what came back has been handled. */
export function forgetSignInStarted(): void {
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing to forget.
  }
}
