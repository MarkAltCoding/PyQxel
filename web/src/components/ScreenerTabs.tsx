import { NavLink } from 'react-router'

/** The Screener's pages: the screen itself, the securities Claude picks from it, and when
 * your own screener is refreshed. */
export function ScreenerTabs() {
  return (
    <nav className="subtabs" aria-label="Screener pages">
      <NavLink to="/screener" end>
        Screen
      </NavLink>
      <NavLink to="/screener/picks">Claude’s Picks</NavLink>
      <NavLink to="/screener/refresh">Refresh Schedule</NavLink>
    </nav>
  )
}
