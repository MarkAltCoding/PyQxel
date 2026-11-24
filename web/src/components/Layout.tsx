import { NavLink, Outlet, useNavigate } from 'react-router'
import { PolicyLinks } from './LegalSection'
import { useAuth } from '../auth/useAuth'
import { useScrollToHash } from '../lib/scrollToHash'
import { AdviceNotice } from './AdviceNotice'
import { SearchBox } from './SearchBox'
import { ThemeToggle } from './ThemeToggle'
import { PageIcon } from './PageIcon'

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
            <PageIcon page="markets" />
            <span>Markets</span>
          </NavLink>
          <NavLink to="/portfolio">
            <PageIcon page="portfolio" />
            <span>Portfolio</span>
          </NavLink>
          <NavLink to="/backtest">
            <PageIcon page="backtest" />
            <span>Backtest</span>
          </NavLink>
          <NavLink to="/screener">
            <PageIcon page="screener" />
            <span>Screener</span>
          </NavLink>
          <NavLink to="/picks">
            <PageIcon page="picks" />
            <span>Picks</span>
          </NavLink>
          <NavLink to="/settings" className="nav-settings">
            <PageIcon page="settings" />
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
      <AdviceNotice />
      <footer className="footer">
        PyQxel helps investors research securities. Analyses, backtests, and simulations are built
        from historical data and are not investment advice. <PolicyLinks />
      </footer>
    </div>
  )
}
