import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getMetrics } from '../api/endpoints'
import type { CompanyMetrics } from '../api/types'
import { compact, money, number, percent, shortDate, signedPercent, tone } from '../lib/format'
import { Notice } from './Notice'
import { Stat } from './Stat'

type Metrics = CompanyMetrics['metrics']

interface Row {
  label: string
  value: (metrics: Metrics, currency: string) => string
}

const ratio = (value: number | null | undefined) => (value == null ? '—' : `${number(value, 1)}×`)

/** Metric groups, each a column of label and value pairs. */
const GROUPS: { title: string; rows: Row[] }[] = [
  {
    title: 'Valuation',
    rows: [
      { label: 'P/E (TTM)', value: (m) => ratio(m.pe_ttm) },
      { label: 'Forward P/E', value: (m) => ratio(m.forward_pe) },
      { label: 'PEG', value: (m) => number(m.peg_ttm) },
      { label: 'Price / sales', value: (m) => ratio(m.ps_ttm) },
      { label: 'Price / book', value: (m) => ratio(m.pb) },
      { label: 'EV / EBITDA', value: (m) => ratio(m.ev_ebitda_ttm) },
      { label: 'Price / free cash flow', value: (m) => ratio(m.price_to_fcf_ttm) },
      { label: 'Enterprise value', value: (m) => compact(m.enterprise_value) },
    ],
  },
  {
    title: 'Profitability and growth',
    rows: [
      { label: 'Gross margin', value: (m) => percent(m.gross_margin_ttm) },
      { label: 'Operating margin', value: (m) => percent(m.operating_margin_ttm) },
      { label: 'Net margin', value: (m) => percent(m.net_margin_ttm) },
      { label: 'Return on equity', value: (m) => percent(m.roe_ttm) },
      { label: 'Return on assets', value: (m) => percent(m.roa_ttm) },
      { label: 'Revenue growth (TTM)', value: (m) => signedPercent(m.revenue_growth_ttm) },
      { label: 'EPS growth (TTM)', value: (m) => signedPercent(m.eps_growth_ttm) },
      { label: 'Revenue growth, 5Y a year', value: (m) => signedPercent(m.revenue_growth_5y) },
    ],
  },
  {
    title: 'Dividends and balance sheet',
    rows: [
      { label: 'Dividend yield', value: (m) => percent(m.dividend_yield, 2) },
      { label: 'Dividend (TTM)', value: (m, c) => money(m.dividend_per_share_ttm, c) },
      { label: 'Payout ratio', value: (m) => percent(m.payout_ratio_ttm) },
      { label: 'Dividend growth, 5Y a year', value: (m) => signedPercent(m.dividend_growth_5y) },
      { label: 'Debt / equity', value: (m) => number(m.debt_to_equity) },
      { label: 'Current ratio', value: (m) => number(m.current_ratio) },
      { label: 'Quick ratio', value: (m) => number(m.quick_ratio) },
      { label: 'Interest coverage', value: (m) => ratio(m.interest_coverage_ttm) },
    ],
  },
]

/** A company's key metrics and peers, from Finnhub's basic financials on the account's key. */
export function MetricsCard({ symbol, currency }: { symbol: string; currency: string }) {
  const metrics = useQuery({
    queryKey: ['metrics', symbol],
    queryFn: () => getMetrics(symbol),
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  })
  const status = metrics.error instanceof ApiError ? metrics.error.status : null
  const data = metrics.data

  return (
    <section className="card" aria-busy={metrics.isLoading}>
      <div className="card-header">
        <div>
          <h2>Key metrics</h2>
          <div className="subtitle">
            Trading, valuation, profitability and balance sheet metrics, as Finnhub computes them
          </div>
        </div>
      </div>
      {status === 403 ? (
        <div className="empty">
          Add a Finnhub key in <Link to="/settings">Settings</Link> to see key metrics and peers.
        </div>
      ) : status === 402 || status === 404 ? (
        <div className="empty">Finnhub has no metrics for {symbol} on your plan.</div>
      ) : metrics.error ? (
        <Notice tone="error">{(metrics.error as Error).message}</Notice>
      ) : !data ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <MetricsBody data={data} currency={currency} />
      )}
    </section>
  )
}

function MetricsBody({ data, currency }: { data: CompanyMetrics; currency: string }) {
  const m = data.metrics
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="stats">
        <Stat
          label="52-week range"
          value={
            m.week_52_low != null && m.week_52_high != null
              ? `${money(m.week_52_low, currency)} – ${money(m.week_52_high, currency)}`
              : '—'
          }
          detail={m.week_52_high_date ? `High on ${shortDate(m.week_52_high_date)}` : undefined}
        />
        <Stat
          label="52-week return"
          value={signedPercent(m.return_52_week)}
          className={tone(m.return_52_week)}
          detail={
            m.relative_to_sp500_52_week != null
              ? `${signedPercent(m.relative_to_sp500_52_week)} vs the S&P 500`
              : undefined
          }
        />
        <Stat label="Beta" value={number(m.beta)} detail="Against the market" />
        <Stat
          label="Average volume"
          value={compact(m.average_volume_3_month, '')}
          detail="Shares a day, 3 months"
        />
      </div>
      <div className="metrics-grid">
        {GROUPS.map((group) => (
          <div key={group.title}>
            <h3>{group.title}</h3>
            <dl className="metric-list">
              {group.rows.map((row) => (
                <div key={row.label}>
                  <dt>{row.label}</dt>
                  <dd>{row.value(m, currency)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
      {data.peers.length > 0 && (
        <div>
          <h3>Peers</h3>
          <div className="filter-row">
            {data.peers.map((peer) => (
              <Link
                key={peer}
                to={`/stocks/${encodeURIComponent(peer.replace('.', '-'))}`}
                className="badge"
              >
                {peer}
              </Link>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
