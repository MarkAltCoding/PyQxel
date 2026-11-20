import { ClaudesPicks } from '../components/ClaudesPicks'
import { ScreenerTabs } from '../components/ScreenerTabs'

/** The Screener's Claude's picks tab: the shared picks, under the screener's header. */
export function PicksPage() {
  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Claude’s picks</h1>
          <div className="subtitle">
            What Claude would buy now and hold for as long as you say, picked from your screener
          </div>
        </div>
        <ScreenerTabs />
      </header>
      <ClaudesPicks />
    </div>
  )
}
