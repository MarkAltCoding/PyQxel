import { useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice } from '../components/Notice'

export function SignInPage() {
  const { apiKey, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [key, setKey] = useState('')
  const [remember, setRemember] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  if (apiKey) return <Navigate to={from} replace />

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

  return (
    <main>
      <section className="card signin">
        <form onSubmit={submit}>
          <div>
            <h1>Sign in to PyQxel</h1>
            <p className="subtitle" style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
              Use the API key you were given (it starts with <code>pq_</code>).
            </p>
          </div>
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
          <p className="disclaimer">
            No key? Whoever runs this PyQxel server creates accounts with{' '}
            <code>python -m app.jobs.users create</code>.
          </p>
        </form>
      </section>
    </main>
  )
}
