import type { ReactNode } from 'react'
import { Link, NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { useScrollToHash } from '../lib/scrollToHash'
import { SearchBox } from './SearchBox'
import { ThemeToggle } from './ThemeToggle'

/** A 24px line icon for a tab; the stylesheet shows it only in the phone's tab bar. */
function TabIcon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="nav-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  )
}

export function Layout() {
  const navigate = useNavigate()
  const { signOut } = useAuth()
  useScrollToHash()
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <img src="/favicon.svg" alt="" />
          PyQxel
        </NavLink>
        {/* At desktop widths a row of links in the top bar; on a phone a tab bar along the
            bottom of the screen, within reach of a thumb, which also holds Settings. */}
        <nav className="nav" aria-label="Main">
          <NavLink to="/markets">
            <TabIcon>
              <circle cx="12" cy="12" r="9" />
              <path d="M3 12h18M12 3c2.5 2.6 3.7 5.6 3.7 9s-1.2 6.4-3.7 9c-2.5-2.6-3.7-5.6-3.7-9S9.5 5.6 12 3z" />
            </TabIcon>
            <span>Markets</span>
          </NavLink>
          <NavLink to="/portfolio">
            <TabIcon>
              <path d="M12 3v9h9" />
              <path d="M20.5 15A9 9 0 1 1 9 3.5" />
            </TabIcon>
            <span>Portfolio</span>
          </NavLink>
          <NavLink to="/backtest">
            <TabIcon>
              <path d="M4 4v16h16" />
              <path d="M7 15l4-4 3 3 5-6" />
            </TabIcon>
            <span>Backtest</span>
          </NavLink>
          <NavLink to="/screener">
            <TabIcon>
              <path d="M4 5h16l-6 7.5V19l-4 1.5v-8z" />
            </TabIcon>
            <span>Screener</span>
          </NavLink>
          <NavLink to="/settings" className="nav-settings">
            <TabIcon>
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
            </TabIcon>
            <span>Settings</span>
          </NavLink>
        </nav>
        <div className="topbar-search">
          <SearchBox
            clearOnSelect
            onSelect={(symbol) => navigate(`/stocks/${encodeURIComponent(symbol)}`)}
          />
        </div>
        <div className="topbar-actions">
          <ThemeToggle />
          <NavLink to="/settings" className="button ghost small topbar-settings">
            Settings
          </NavLink>
          <button type="button" className="button ghost small" onClick={signOut}>
            Sign out
          </button>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        PyQxel helps investors research securities. Analyses, backtests and simulations are built
        from historical data and are not investment advice. <Link to="/privacy">Privacy</Link>
      </footer>
    </div>
  )
}
