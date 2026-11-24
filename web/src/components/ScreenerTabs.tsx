import { NavLink } from 'react-router'

/** The Screener's pages: the screen itself, and when your own screener is refreshed. */
export function ScreenerTabs() {
  return (
    <nav className="subtabs" aria-label="Screener pages">
      <NavLink to="/screener" end>
        Screen
      </NavLink>
      <NavLink to="/screener/refresh">Refresh Schedule</NavLink>
    </nav>
  )
}
