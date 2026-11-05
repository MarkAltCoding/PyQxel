import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { apiUrl } from '../api/client'
import { collectAppSignIn, getSignInProviders } from '../api/endpoints'
import {
  forgetNativeSignIn,
  isNativeApp,
  pendingVerifier,
  startNativeSignIn,
} from '../auth/nativeSignIn'
import {
  forgetSignInStarted,
  markSignInStarted,
  signInStartedRecently,
} from '../auth/pendingSignIn'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice, Notice } from '../components/Notice'

/** What a GitHub, Google or Apple sign-in sends back in the URL fragment, which never
 * reaches a server: an API key (or, in the native app, a one-time code for one) and the
 * page to return to, or why signing in failed. */
function readFragment(hash: string): {
  key: string | null
  code: string | null
  next: string
  error: string | null
} {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  const next = params.get('next') ?? '/'
  return {
    key: params.get('key'),
    code: params.get('code'),
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
  /** Whether this page runs in the native app, which signs in through the system browser. */
  const native = isNativeApp()
  /** Set once the native app has opened a sign-in in the system browser. */
  const [waitingFor, setWaitingFor] = useState<string | null>(null)
  // Read on every change, since a sign-in can also come back to a page already open.
  const fragment = useMemo(() => readFragment(location.hash), [location.hash])
  const [returnTo, setReturnTo] = useState<string | null>(null)
  /** The fragment being handled, so the same one is not handled twice. */
  const finishing = useRef<string | null>(null)
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const providers = useQuery({ queryKey: ['sign-in-providers'], queryFn: getSignInProviders })
  const offered = Array.isArray(providers.data) ? providers.data : []

  // What came back is trusted only when this tab (or, in the native app, the app) started
  // the sign-in, so a link carrying someone else's key, or a message of their own, is
  // ignored. The website takes a key; the app only a code, which it collects its key with.
  const credential = native ? fragment.code : fragment.key
  const returned = Boolean(credential || fragment.error)
  // Read once for each fragment that comes back, before the effect uses up the mark.
  const { verifier, trusted } = useMemo(() => {
    const waiting = native && (credential || fragment.error) ? pendingVerifier() : null
    const started = native ? waiting !== null : signInStartedRecently()
    return { verifier: waiting, trusted: Boolean(credential || fragment.error) && started }
  }, [native, credential, fragment.error])
  const returnedError = !returned
    ? null
    : !trusted
      ? native
        ? 'That sign-in was not started in this app, or took too long, so it was ignored. Sign in below.'
        : 'That sign-in was not started in this browser tab, so it was ignored. Sign in below.'
      : credential
        ? null
        : fragment.error

  // It is taken out of the address bar and history at once, then a trusted key is kept.
  useEffect(() => {
    if (!returned || finishing.current === location.hash) return
    finishing.current = location.hash
    window.history.replaceState(window.history.state, '', location.pathname)
    forgetSignInStarted()
    forgetNativeSignIn()
    setWaitingFor(null)
    if (!trusted || !credential) return
    const key =
      native && verifier
        ? collectAppSignIn(credential, verifier).then((issued) => issued.key)
        : Promise.resolve(credential)
    key
      .then((issued) => signIn(issued, true))
      .then(() => setReturnTo(fragment.next))
      .catch((caught: unknown) => setError(caught))
  }, [
    native,
    returned,
    trusted,
    credential,
    verifier,
    fragment,
    location.hash,
    location.pathname,
    signIn,
  ])

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

  /** Opens the sign-in in the system browser; the app is opened again when it is done. */
  const openInBrowser = (provider: string, label: string) => {
    setError(null)
    startNativeSignIn(provider, from)
      .then(() => setWaitingFor(label))
      .catch((caught: unknown) => setError(caught))
  }

  if (credential && error === null && returnedError === null) {
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
        {waitingFor && (
          <Notice>
            Finish signing in with {waitingFor} in your browser. PyQxel opens again when you are
            done.
          </Notice>
        )}
        {offered.length > 0 && (
          <div className="stack" style={{ gap: 10 }}>
            {offered.map((provider) =>
              native ? (
                <button
                  key={provider.name}
                  type="button"
                  className="button signin-provider"
                  onClick={() => openInBrowser(provider.name, provider.label)}
                >
                  Continue with {provider.label}
                </button>
              ) : (
                <a
                  key={provider.name}
                  className="button signin-provider"
                  onClick={markSignInStarted}
                  href={apiUrl(
                    `/api/v1/auth/${provider.name}/start?${new URLSearchParams({ next: from })}`,
                  )}
                >
                  Continue with {provider.label}
                </a>
              ),
            )}
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
          PyQxel is a research tool, not investment advice.{' '}
          <Link to="/privacy">Privacy policy</Link>
        </p>
      </section>
    </main>
  )
}
