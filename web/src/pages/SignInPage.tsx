import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getSignInProviders } from '../api/endpoints'
import {
  forgetSignInStarted,
  markSignInStarted,
  signInStartedRecently,
} from '../auth/pendingSignIn'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice, Notice } from '../components/Notice'

/** What a GitHub or Google sign-in sends back in the URL fragment, which never reaches a
 * server: an API key and the page to return to, or why signing in failed. */
function readFragment(hash: string): { key: string | null; next: string; error: string | null } {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const next = params.get('next') ?? '/'
  return {
    key: params.get('key'),
    // Only pages on this site, as the server also checks.
    next: next.startsWith('/') && !next.startsWith('//') ? next : '/',
    error: params.get('error'),
  }
}

export function SignInPage() {
  const { apiKey, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [key, setKey] = useState('')
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  // Read on every change, since a sign-in can also come back to a page already open.
  const fragment = useMemo(() => readFragment(location.hash), [location.hash])
  const [returnTo, setReturnTo] = useState<string | null>(null)
  /** The fragment being handled, so the same one is not handled twice. */
  const finishing = useRef<string | null>(null)
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const providers = useQuery({ queryKey: ['sign-in-providers'], queryFn: getSignInProviders })
  const offered = Array.isArray(providers.data) ? providers.data : []

  // What GitHub or Google sent back is trusted only when this tab started the sign-in,
  // so a link carrying someone else's key, or a message of their own, is ignored.
  const returned = Boolean(fragment.key || fragment.error)
  // Read once for each fragment that comes back, before the effect uses up the mark.
  const trusted = useMemo(
    () => Boolean(fragment.key || fragment.error) && signInStartedRecently(),
    [fragment],
  )
  const returnedError = !returned
    ? null
    : !trusted
      ? 'That sign-in was not started in this browser tab, so it was ignored. Sign in below.'
      : fragment.key
        ? null
        : fragment.error

  // It is taken out of the address bar and history at once, then a trusted key is kept.
  useEffect(() => {
    if (!returned || finishing.current === location.hash) return
    finishing.current = location.hash
    window.history.replaceState(window.history.state, '', location.pathname)
    forgetSignInStarted()
    if (!trusted || !fragment.key) return
    signIn(fragment.key, true)
      .then(() => setReturnTo(fragment.next))
      .catch((caught: unknown) => setError(caught))
  }, [returned, trusted, fragment, location.hash, location.pathname, signIn])

  if (apiKey) return <Navigate to={returnTo ?? from} replace />

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await signIn(key, remember)
      navigate(from, { replace: true })
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  if (fragment.key && error === null && returnedError === null) {
    return (
      <main>
        <section className="card signin">
          <p className="subtitle">Signing you in…</p>
        </section>
      </main>
    )
  }

  return (
    <main>
      <section className="card signin stack" style={{ gap: 18 }}>
        <div>
          <h1>Sign in to PyQxel</h1>
          {offered.length > 0 && (
            <p className="subtitle" style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
              New here? Signing in creates your account.
            </p>
          )}
        </div>
        {returnedError && <Notice tone="error">{returnedError}</Notice>}
        {offered.length > 0 && (
          <div className="stack" style={{ gap: 10 }}>
            {offered.map((provider) => (
              <a
                key={provider.name}
                className="button signin-provider"
                onClick={markSignInStarted}
                href={`/api/v1/auth/${provider.name}/start?${new URLSearchParams({ next: from })}`}
              >
                Continue with {provider.label}
              </a>
            ))}
          </div>
        )}
        <form onSubmit={submit}>
          {offered.length > 0 && <div className="signin-divider">or use an API key</div>}
          {offered.length === 0 && (
            <p className="subtitle" style={{ color: 'var(--text-secondary)' }}>
              Use your API key (it starts with <code>pq_</code>).
            </p>
          )}
          <label className="field">
            <span>API key</span>
            <input
              type="password"
              autoComplete="current-password"
              spellCheck={false}
              required
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="pq_…"
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            Keep me signed in on this browser
          </label>
          {error !== null && <ErrorNotice error={error} />}
          <button type="submit" className="button primary" disabled={busy || !key.trim()}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
          {offered.length === 0 && (
            <p className="disclaimer">
              No key? Whoever runs this PyQxel server creates accounts with{' '}
              <code>python -m app.jobs.users create</code>, or can turn on signing in with GitHub or
              Google.
            </p>
          )}
        </form>
        <p className="disclaimer">
          <Link to="/privacy">Privacy policy</Link>
        </p>
      </section>
    </main>
  )
}
