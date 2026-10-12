import { Link, useNavigate } from 'react-router'
import { SearchBox } from '../components/SearchBox'

const SHORTCUTS = [
  {
    to: '/portfolio',
    title: 'Portfolio',
    text: 'Your holdings, simulated outcomes and how they move together.',
  },
  {
    to: '/backtest',
    title: 'Backtest',
    text: 'Test momentum, trend and mean-reversion rules against buy-and-hold.',
  },
  {
    to: '/screener',
    title: 'Screener',
    text: 'Filter US stocks on valuation, quality, momentum and factor betas.',
  },
]

export function HomePage() {
  const navigate = useNavigate()
  return (
    <>
      <section className="hero-search">
        <h1>Research a security</h1>
        <p>Prices, factor exposures, volatility and an AI-written thesis, in one place.</p>
        <SearchBox
          large
          autoFocus
          onSelect={(symbol) => navigate(`/stocks/${encodeURIComponent(symbol)}`)}
        />
      </section>
      <div className="shortcut-grid">
        {SHORTCUTS.map((shortcut) => (
          <Link key={shortcut.to} to={shortcut.to} className="card">
            <h2>{shortcut.title}</h2>
            <p>{shortcut.text}</p>
          </Link>
        ))}
      </div>
    </>
  )
}
