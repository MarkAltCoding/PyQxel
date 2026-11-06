import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getFund } from '../api/endpoints'
import type { FundProfile } from '../api/types'
import { compact, percent, shortDate } from '../lib/format'
import { InfoTip } from './InfoTip'
import { Notice } from './Notice'
import { Stat } from './Stat'

/** What an ETF or mutual fund holds and costs, from Alpha Vantage's fund profile on the
 * account's key: the figures a fund has in place of a company's market cap and earnings. */
export function FundCard({ symbol }: { symbol: string }) {
  const fund = useQuery({
    queryKey: ['fund', symbol],
    queryFn: () => getFund(symbol),
    // A day's answer is kept on the server; Alpha Vantage's free plan allows 25 a day.
    staleTime: 60 * 60 * 1000,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
  })
  const status = fund.error instanceof ApiError ? fund.error.status : null

  return (
    <section id="fund" className="card" aria-busy={fund.isLoading}>
      <div className="card-header">
        <div>
          <h2>Holdings and costs</h2>
          <div className="subtitle">What the fund holds and charges, from Alpha Vantage</div>
        </div>
      </div>
      {status === 403 ? (
        <div className="empty">
          Add an Alpha Vantage key in <Link to="/settings">Settings</Link> to see this fund’s
          holdings and costs: its free plan serves them.
        </div>
      ) : status === 404 ? (
        <div className="empty">Alpha Vantage has no holdings or costs for {symbol}.</div>
      ) : fund.error ? (
        <Notice tone="error">{(fund.error as Error).message}</Notice>
      ) : !fund.data ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <FundBody fund={fund.data} />
      )}
    </section>
  )
}

function FundBody({ fund }: { fund: FundProfile }) {
  const sectors = fund.sectors ?? []
  const holdings = fund.holdings ?? []
  const topWeight = holdings.reduce((sum, holding) => sum + holding.weight, 0)
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="stats">
        <Stat info="netAssets" label="Net assets" value={compact(fund.net_assets)} />
        <Stat
          info="expenseRatio"
          label="Expense ratio"
          value={percent(fund.expense_ratio, 2)}
          detail={
            fund.expense_ratio != null
              ? `$${Math.round(fund.expense_ratio * 10_000)} a year per $10,000`
              : undefined
          }
        />
        <Stat
          info="fundDividendYield"
          label="Distribution yield"
          value={percent(fund.dividend_yield, 2)}
        />
        <Stat
          info="portfolioTurnover"
          label="Turnover"
          value={percent(fund.portfolio_turnover, 0)}
        />
        <Stat
          info="inceptionDate"
          label="Started"
          value={fund.inception_date ? shortDate(fund.inception_date) : '—'}
        />
        <Stat
          info="leveragedFund"
          label="Leveraged"
          value={fund.leveraged == null ? '—' : fund.leveraged ? 'Yes' : 'No'}
        />
      </div>
      <div className="metrics-grid">
        {sectors.length > 0 && (
          <div>
            <div className="heading-with-info">
              <h3>Sector weights</h3>
              <InfoTip topic="fundSectors" />
            </div>
            <table className="data">
              <thead>
                <tr>
                  <th>Sector</th>
                  <th className="num">Weight</th>
                </tr>
              </thead>
              <tbody>
                {sectors
                  .filter((sector) => sector.weight > 0)
                  .map((sector) => (
                    <tr key={sector.name}>
                      <td>{sector.name}</td>
                      <td className="num">{percent(sector.weight, 1)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
        {holdings.length > 0 && (
          <div>
            <div className="heading-with-info">
              <h3>Largest holdings</h3>
              <InfoTip topic="fundHoldings" />
            </div>
            <table className="data">
              <thead>
                <tr>
                  <th>Holding</th>
                  <th className="num">Weight</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((holding) => (
                  <tr key={`${holding.symbol ?? ''}-${holding.name}`}>
                    <td>
                      {holding.symbol ? (
                        <Link to={`/stocks/${encodeURIComponent(holding.symbol)}`}>
                          {holding.symbol}
                        </Link>
                      ) : null}{' '}
                      <span className="subtitle">{holding.name}</span>
                    </td>
                    <td className="num">{percent(holding.weight, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="disclaimer">
              The top {holdings.length} hold {percent(topWeight, 0)} of the fund
              {fund.holdings_count ? `, among ${fund.holdings_count.toLocaleString()} listed` : ''}.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
