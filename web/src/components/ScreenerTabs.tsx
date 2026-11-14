import { NavLink } from 'react-router'

/** The Screener's two pages: the screen itself, and the securities Claude picks from it. */
export function ScreenerTabs() {
  return (
    <nav className="subtabs" aria-label="Screener pages">
      <NavLink to="/screener" end>
        Screen
      </NavLink>
      <NavLink to="/screener/picks">Claude’s picks</NavLink>
    </nav>
  )
}
