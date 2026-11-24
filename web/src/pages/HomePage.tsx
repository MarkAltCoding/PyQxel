import { Link, useNavigate } from 'react-router'
import { PageIcon, type IconPage } from '../components/PageIcon'
import { SearchBox } from '../components/SearchBox'

const SHORTCUTS: { to: string; page: IconPage; title: string; text: string }[] = [
  {
    to: '/markets',
    page: 'markets',
    title: 'Markets',
    text: 'Market news and the calendar of earnings, IPOs, holidays, and Fed meetings.',
  },
  {
    to: '/portfolio',
    page: 'portfolio',
    title: 'Portfolio',
    text: 'Your holdings, simulated outcomes, and an AI review with a plan.',
  },
  {
    to: '/backtest',
    page: 'backtest',
    title: 'Backtest',
    text: 'Test trading rules or your own code against buying and holding.',
  },
  {
    to: '/screener',
    page: 'screener',
    title: 'Screener',
    text: 'Filter US stocks on valuation, quality, momentum, and risk.',
  },
  {
    to: '/picks',
    page: 'picks',
    title: 'Claude’s Picks',
    text: 'What Claude would buy from your screener, and how its picks have done.',
  },
]

export function HomePage() {
  const navigate = useNavigate()
  return (
    <>
      <section className="hero-search">
        <h1>Research a Security</h1>
        <p>Prices, factor exposures, volatility, and an AI-written thesis, in one place.</p>
        <SearchBox
          large
          autoFocus
          onSelect={(symbol) => navigate(`/stocks/${encodeURIComponent(symbol)}`)}
        />
      </section>
      <div className="shortcut-grid">
        {SHORTCUTS.map((shortcut) => (
          <Link key={shortcut.to} to={shortcut.to} className="card">
            <span className="shortcut-icon">
              <PageIcon page={shortcut.page} className="shortcut-svg" />
            </span>
            <h2>{shortcut.title}</h2>
            <p>{shortcut.text}</p>
          </Link>
        ))}
      </div>
    </>
  )
}
