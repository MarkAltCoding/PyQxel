import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getFund } from '../api/endpoints'
import type { FundProfile } from '../api/types'
import { compact, listed, number, percent, shortDate, signedPercent } from '../lib/format'
import { InfoTip } from './InfoTip'
import { Notice } from './Notice'
import { Stat } from './Stat'

const SOURCE_NAMES: Record<string, string> = { alpha_vantage: 'Alpha Vantage', sec: 'the SEC' }

/** What an ETF or mutual fund holds and costs, and the figures of the companies it holds:
 * the fund's stand-in for a company's market cap, valuation, and earnings. Costs come from
 * Alpha Vantage on the account's key, holdings from it or from the fund's SEC filings, and
 * the companies' figures from the account's screener or Finnhub. */
export function FundCard({ symbol }: { symbol: string }) {
  const fund = useQuery({
    queryKey: ['fund', symbol],
    queryFn: () => getFund(symbol),
    // The server keeps each source's answer for a day or more.
    staleTime: 60 * 60 * 1000,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 1,
  })
  const status = fund.error instanceof ApiError ? fund.error.status : null
  const data = fund.data
  const sources = data
    ? listed(data.source.split(',').map((name) => SOURCE_NAMES[name.trim()] ?? name.trim()))
    : undefined

  return (
    <section id="fund" className="card" aria-busy={fund.isLoading}>
      <div className="card-header">
        <div>
          <h2>Holdings and costs</h2>
          <div className="subtitle">
            What the fund holds and charges{sources ? `, from ${sources}` : ''}
            {data?.holdings_as_of ? ` · holdings as of ${shortDate(data.holdings_as_of)}` : ''}
          </div>
        </div>
      </div>
      {status === 403 || status === 404 ? (
        <div className="empty">{(fund.error as Error).message}</div>
      ) : fund.error ? (
        <Notice tone="error">{(fund.error as Error).message}</Notice>
      ) : !data ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <FundBody fund={data} />
      )}
    </section>
  )
}

function FundBody({ fund }: { fund: FundProfile }) {
  const sectors = (fund.sectors ?? []).filter((sector) => sector.weight > 0)
  const holdings = fund.holdings ?? []
  const mix = (fund.asset_mix ?? []).filter((part) => part.weight >= 0.001)
  const topWeight = holdings.reduce((sum, holding) => sum + holding.weight, 0)
  const hasCosts =
    fund.expense_ratio != null || fund.dividend_yield != null || fund.portfolio_turnover != null
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="stats">
        <Stat info="netAssets" label="Net assets" value={compact(fund.net_assets)} />
        {hasCosts && (
          <>
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
          </>
        )}
        {fund.inception_date && (
          <Stat info="inceptionDate" label="Started" value={shortDate(fund.inception_date)} />
        )}
        {fund.leveraged != null && (
          <Stat info="leveragedFund" label="Leveraged" value={fund.leveraged ? 'Yes' : 'No'} />
        )}
        {mix.map((part) => (
          <Stat
            key={part.name}
            info="assetMix"
            label={part.name}
            value={percent(part.weight, 1)}
            detail="Of net assets"
          />
        ))}
      </div>

      {fund.stats && <WeightedStats fund={fund} />}

      {fund.bonds && (
        <div>
          <div className="heading-with-info">
            <h3>Its bonds</h3>
            <InfoTip topic="bondIssuers" />
          </div>
          <div className="stats">
            <Stat
              info="bondCoupon"
              label="Average coupon"
              value={percent(fund.bonds.average_coupon, 2)}
            />
            <Stat
              info="bondMaturity"
              label="Average maturity"
              value={
                fund.bonds.average_maturity_years != null
                  ? `${number(fund.bonds.average_maturity_years, 1)} years`
                  : '—'
              }
              detail="Stated final dates"
            />
            {Object.entries(fund.bonds.issuers ?? {}).map(([kind, share]) => (
              <Stat
                key={kind}
                info="bondIssuers"
                label={kind}
                value={percent(share, 1)}
                detail="Of its bonds"
              />
            ))}
          </div>
          <p className="disclaimer">
            {fund.bonds.count.toLocaleString()} bonds
            {fund.holdings_as_of ? `, as of ${shortDate(fund.holdings_as_of)}` : ''}.
          </p>
        </div>
      )}

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
                {sectors.map((sector) => (
                  <tr key={sector.name}>
                    <td>{sector.name}</td>
                    <td className="num">{percent(sector.weight, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {fund.stats && Object.keys(fund.stats.sectors ?? {}).length > 0 && (
              <p className="disclaimer">
                By its companies’ SEC industry codes, for the{' '}
                {percent(fund.stats.sector_coverage, 0)} of its stocks classified.
              </p>
            )}
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
                {holdings.map((holding, index) => (
                  <tr key={`${holding.symbol ?? ''}-${holding.name}-${index}`}>
                    <td>
                      {holding.symbol ? (
                        <>
                          <Link to={`/stocks/${encodeURIComponent(holding.symbol)}`}>
                            {holding.symbol}
                          </Link>{' '}
                          <span className="subtitle">{holding.name}</span>
                        </>
                      ) : (
                        holding.name
                      )}
                    </td>
                    <td className="num">{percent(holding.weight, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="disclaimer">
              The top {holdings.length} hold {percent(topWeight, 0)} of the fund
              {fund.holdings_count ? `, among ${fund.holdings_count.toLocaleString()}` : ''}.
            </p>
          </div>
        )}
      </div>

      {(fund.notes ?? []).map((note) => (
        <Notice key={note}>{note}</Notice>
      ))}
    </div>
  )
}

const ratio = (value: number | null | undefined) => (value == null ? '—' : `${number(value, 1)}×`)

/** The fund's figures weighted from the companies it holds, with their dates. */
function WeightedStats({ fund }: { fund: FundProfile }) {
  const stats = fund.stats
  if (!stats) return null
  const size = stats.size_mix ?? {}
  const from = listed(fund.stats_sources ?? [])
  return (
    <div>
      <div className="heading-with-info">
        <h3>Weighted from its holdings</h3>
        <InfoTip topic="weightedFromHoldings" />
      </div>
      <p className="subtitle" style={{ marginTop: 2 }}>
        Its companies’ figures{from ? ` from ${from}` : ''}
        {fund.stats_as_of ? ` as of ${shortDate(fund.stats_as_of)}` : ''}, weighted by the holdings
        {fund.holdings_as_of ? ` as of ${shortDate(fund.holdings_as_of)}` : ''}; they cover{' '}
        {percent(stats.coverage, 0)} of its stocks.
      </p>
      <div className="stats">
        <Stat info="fundPe" label="P/E" value={ratio(stats.pe_ratio)} detail="Weighted" />
        <Stat
          info="fundPriceSales"
          label="Price / sales"
          value={ratio(stats.price_to_sales)}
          detail="Weighted"
        />
        <Stat
          info="fundMargins"
          label="Net margin"
          value={percent(stats.net_margin)}
          detail={`Operating ${percent(stats.operating_margin)} · gross ${percent(stats.gross_margin)}`}
        />
        <Stat info="fundRoe" label="Return on equity" value={percent(stats.return_on_equity)} />
        <Stat
          info="fundGrowth"
          label="Revenue growth"
          value={signedPercent(stats.revenue_growth)}
          detail={`Earnings ${signedPercent(stats.earnings_growth)}`}
        />
        <Stat
          info="fundSize"
          label="Average company"
          value={compact(stats.average_market_cap)}
          detail={
            size.large != null
              ? `Large ${percent(size.large, 0)} · mid ${percent(size.mid ?? 0, 0)} · small ${percent(size.small ?? 0, 0)}`
              : undefined
          }
        />
      </div>
    </div>
  )
}
