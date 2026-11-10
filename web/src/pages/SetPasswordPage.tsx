import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { PolicyLinks } from '../components/LegalSection'
import { useQuery } from '@tanstack/react-query'
import { checkPasswordLink, getPasswordSignIn, setPassword } from '../api/endpoints'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice, Notice } from '../components/Notice'

/** The token of the emailed link, read once from the fragment and then taken out of the
 * address bar and history, so it is not left behind in either. */
function takeToken(): string | null {
  const token = new URLSearchParams(window.location.hash.replace(/^#/, '')).get('token')
  if (token) window.history.replaceState(window.history.state, '', window.location.pathname)
  return token
}

/** Where an emailed link leads: choosing the password that makes an account, or a new
 * one after forgetting it. The link proves the address is the reader's. */
export function SetPasswordPage() {
  const { signIn } = useAuth()
  const navigate = useNavigate()
  const [token] = useState(takeToken)
  const [password, setPasswordText] = useState('')
  const [again, setAgain] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const info = useQuery({ queryKey: ['password-sign-in'], queryFn: getPasswordSignIn })
  const minLength = info.data?.min_length ?? 12
  const link = useQuery({
    queryKey: ['password-link', token],
    queryFn: () => checkPasswordLink(token ?? ''),
    enabled: token !== null,
    retry: false,
    staleTime: Infinity,
  })

  useEffect(() => {
    document.title = 'Choose a password · PyQxel'
  }, [])

  const mismatch = again.length > 0 && again !== password
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!token || mismatch) return
    setBusy(true)
    setError(null)
    try {
      const issued = await setPassword(token, password)
      await signIn(issued.key, true)
      navigate('/', { replace: true })
    } catch (caught) {
      setError(caught)
      setBusy(false)
    }
  }

  const broken = token === null || link.isError
  return (
    <main>
      <section className="card signin stack" style={{ gap: 18 }}>
        <div>
          <h1>Choose a password</h1>
          {link.data && (
            <p className="subtitle" style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
              For the PyQxel account at <strong>{link.data.email}</strong>. You sign in with this
              email and password from now on, on the website and in the app.
            </p>
          )}
        </div>
        {broken ? (
          <>
            {link.isError ? (
              <ErrorNotice error={link.error} />
            ) : (
              <Notice tone="error">
                This page needs the link from your email. Open the link exactly as it came, or ask
                for a new one.
              </Notice>
            )}
            <Link to="/signin" className="button primary signin-provider">
              Back to sign in
            </Link>
          </>
        ) : (
          <form onSubmit={submit}>
            {/* For password managers, which save the password under this address. */}
            <input
              type="email"
              autoComplete="username"
              value={link.data?.email ?? ''}
              readOnly
              hidden
            />
            <label className="field">
              <span>New password</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={minLength}
                value={password}
                onChange={(event) => setPasswordText(event.target.value)}
              />
              <small>
                At least {minLength} characters. A few unrelated words make a long password that is
                easy to remember.
              </small>
            </label>
            <label className="field">
              <span>Type it again</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                value={again}
                onChange={(event) => setAgain(event.target.value)}
                aria-invalid={mismatch}
              />
              {mismatch && <small>The two passwords are not the same yet.</small>}
            </label>
            {error !== null && <ErrorNotice error={error} />}
            <button
              type="submit"
              className="button primary"
              disabled={busy || !link.data || password.length < minLength || again !== password}
            >
              {busy ? 'Saving…' : 'Save password and sign in'}
            </button>
            <Link to="/signin" className="link-button">
              Back to sign in
            </Link>
          </form>
        )}
        <p className="disclaimer">
          PyQxel is a research tool, not investment advice. <PolicyLinks />
        </p>
      </section>
    </main>
  )
}
