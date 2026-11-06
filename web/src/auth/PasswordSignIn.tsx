import { useState, type FormEvent } from 'react'
import { sendPasswordLink, signInWithPassword } from '../api/endpoints'
import { ErrorNotice, Notice } from '../components/Notice'

/** Signing in with an email and password, or asking for a link by email to make an
 * account or choose a new password. Both happen in the page itself, so the native app
 * needs no browser for them; only the emailed link opens the website. */
export function PasswordSignIn({
  native,
  minLength,
  onSignedIn,
}: {
  native: boolean
  minLength: number
  onSignedIn: (key: string) => Promise<void>
}) {
  /** Signing in, or asking for a link: to make an account, or after forgetting. */
  const [mode, setMode] = useState<'sign-in' | 'create' | 'forgot'>('sign-in')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [sent, setSent] = useState<string | null>(null)

  const switchTo = (next: typeof mode) => {
    setMode(next)
    setError(null)
    setSent(null)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'sign-in') {
        const issued = await signInWithPassword(email.trim(), password, native)
        await onSignedIn(issued.key)
      } else {
        const answer = await sendPasswordLink(email.trim())
        setSent(answer.message)
      }
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const emailField = (
    <label className="field">
      <span>Email</span>
      <input
        type="email"
        autoComplete="username"
        inputMode="email"
        autoCapitalize="none"
        spellCheck={false}
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
      />
    </label>
  )

  if (mode !== 'sign-in') {
    return (
      <form onSubmit={submit} aria-label={mode === 'create' ? 'Make an account' : 'Reset password'}>
        <p style={{ color: 'var(--text-secondary)' }}>
          {mode === 'create'
            ? 'Enter your email and we will send you a link. Open it to choose your password, and your account is made.'
            : 'Enter your account’s email and we will send you a link to choose a new password.'}
        </p>
        {emailField}
        {sent && (
          <Notice>
            {sent}
            {native &&
              ' The link opens in your browser; once you have chosen your password, sign in here.'}
          </Notice>
        )}
        {error !== null && <ErrorNotice error={error} />}
        <button type="submit" className="button primary" disabled={busy || !email.trim()}>
          {busy ? 'Sending…' : sent ? 'Send again' : 'Email me a link'}
        </button>
        <button type="button" className="link-button" onClick={() => switchTo('sign-in')}>
          Back to signing in with a password
        </button>
      </form>
    )
  }

  return (
    <form onSubmit={submit} aria-label="Sign in with email">
      {emailField}
      <label className="field">
        <span>Password</span>
        <input
          type="password"
          autoComplete="current-password"
          required
          minLength={1}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </label>
      {error !== null && <ErrorNotice error={error} />}
      <button
        type="submit"
        className="button primary"
        disabled={busy || !email.trim() || !password}
      >
        {busy ? 'Signing in…' : 'Sign in'}
      </button>
      <div className="signin-links">
        <button type="button" className="link-button" onClick={() => switchTo('create')}>
          Make an account
        </button>
        <button type="button" className="link-button" onClick={() => switchTo('forgot')}>
          Forgot password?
        </button>
      </div>
      <p className="disclaimer" style={{ margin: 0 }}>
        New passwords need at least {minLength} characters.
      </p>
    </form>
  )
}
