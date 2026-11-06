import { Link, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { Layout } from './Layout'

/**
 * The frame of a page anyone may read, such as the privacy policy: the app's own layout,
 * with every tab, for someone signed in, and otherwise a bar with a way back and a way to
 * sign in, so no page is a dead end on a phone or in the app.
 */
export function PublicLayout() {
  const { apiKey } = useAuth()
  const navigate = useNavigate()
  if (apiKey) return <Layout />
  // A page opened directly has nothing to go back to, so Back goes to signing in.
  const back = () => (window.history.length > 1 ? navigate(-1) : navigate('/signin'))
  return (
    <div className="shell">
      <header className="topbar public-topbar">
        <Link to="/signin" className="brand">
          <img src="/favicon.svg" alt="" />
          PyQxel
        </Link>
        <div className="topbar-actions">
          <button type="button" className="button ghost small" onClick={back}>
            ← Back
          </button>
          <Link to="/signin" className="button primary small">
            Sign in
          </Link>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  )
}
