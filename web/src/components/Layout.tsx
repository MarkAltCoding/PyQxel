import { NavLink, Outlet, useNavigate } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { SearchBox } from './SearchBox'
import { ThemeToggle } from './ThemeToggle'

export function Layout() {
  const navigate = useNavigate()
  const { signOut } = useAuth()
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand">
          <img src="/favicon.svg" alt="" />
          PyQxel
        </NavLink>
        <nav className="nav" aria-label="Main">
          <NavLink to="/portfolio">Portfolio</NavLink>
          <NavLink to="/backtest">Backtest</NavLink>
          <NavLink to="/screener">Screener</NavLink>
        </nav>
        <div className="topbar-search">
          <SearchBox
            clearOnSelect
            onSelect={(symbol) => navigate(`/stocks/${encodeURIComponent(symbol)}`)}
          />
        </div>
        <div className="topbar-actions">
          <ThemeToggle />
          <NavLink to="/settings" className="button ghost small">
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
        from historical data and are not investment advice.
      </footer>
    </div>
  )
}
