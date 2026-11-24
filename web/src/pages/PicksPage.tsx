import { Link } from 'react-router'
import { ClaudesPicks } from '../components/ClaudesPicks'

/** Claude's Picks: what Claude would buy from your screener, with the saved lists and how
 * they have done. The Screener and the Portfolio page link here. */
export function PicksPage() {
  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Claude’s Picks</h1>
          <div className="subtitle">
            What Claude would buy now from your <Link to="/screener">screener</Link>, and how its
            earlier picks have done
          </div>
        </div>
      </header>
      <ClaudesPicks />
    </div>
  )
}
