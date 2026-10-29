import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getSignals } from '../api/endpoints'
import type { MarketSignals } from '../api/types'
import { compact, money, number, percent, shortDate, signedPercent, tone } from '../lib/format'
import { Notice } from './Notice'
import { Stat } from './Stat'

type Month = MarketSignals['recommendations'][number]
type Quarter = MarketSignals['earnings'][number]

const HOURS: Record<string, string> = {
  bmo: 'Before the open',
  amc: 'After the close',
  dmh: 'During market hours',
}

const count = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

const monthName = (period: string) =>
  new Date(`${period}T00:00:00`).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })

const quarterName = (quarter: Quarter) =>
  quarter.quarter && quarter.year
    ? `Q${quarter.quarter} ${quarter.year}`
    : shortDate(quarter.period)

/**
 * What analysts, earnings and insiders say about a stock, from Finnhub on the account's
 * own key.
 */
export function SignalsCard({ symbol, currency }: { symbol: string; currency: string }) {
  const signals = useQuery({
    queryKey: ['signals', symbol],
    queryFn: () => getSignals(symbol),
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  })
  const noKey = signals.error instanceof ApiError && signals.error.status === 403
  const data = signals.data

  return (
    <section className="card" aria-busy={signals.isLoading}>
      <div className="card-header">
        <div>
          <h2>Analysts, earnings and insiders</h2>
          <div className="subtitle">
            Analyst ratings, earnings against estimates, and insiders' open-market trades, from
            Finnhub
          </div>
        </div>
      </div>
      {noKey ? (
        <div className="empty">
          Add a Finnhub key in <Link to="/settings">Settings</Link> to see analyst ratings, earnings
          dates and insider trades here and give them to AI reports.
        </div>
      ) : signals.error ? (
        <Notice tone="error">{(signals.error as Error).message}</Notice>
      ) : !data ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <Signals signals={data} currency={currency} />
      )}
    </section>
  )
}

function Signals({ signals, currency }: { signals: MarketSignals; currency: string }) {
  const latest = signals.recommendations[0]
  const total = latest
    ? latest.strong_buy + latest.buy + latest.hold + latest.sell + latest.strong_sell
    : 0
  const bullish = latest ? (latest.strong_buy + latest.buy) / total : null
  const lastQuarter = signals.earnings[0]
  const next = signals.next_earnings
  const insiders = signals.insiders
  // The average of the last three months with insider trading, which Finnhub reports.
  const recentSentiment = (signals.insider_sentiment ?? []).slice(0, 3)
  const sentiment = recentSentiment.length
    ? recentSentiment.reduce((sum, month) => sum + month.mspr, 0) / recentSentiment.length
    : null
  return (
    <div className="stack" style={{ gap: 14 }}>
      {signals.notice && <Notice tone="warning">{signals.notice}</Notice>}
      <div className="stats">
        <Stat
          info="analystsAtBuy"
          label="Analysts at buy"
          value={percent(bullish, 0)}
          detail={
            latest
              ? `${latest.strong_buy + latest.buy} buy · ${latest.hold} hold · ${latest.sell + latest.strong_sell} sell`
              : 'No coverage'
          }
        />
        <Stat
          info="nextEarnings"
          label="Next earnings"
          value={next ? shortDate(next.date) : '—'}
          detail={
            next
              ? [
                  next.hour && HOURS[next.hour],
                  next.eps_estimate != null && `EPS est. ${number(next.eps_estimate)}`,
                ]
                  .filter(Boolean)
                  .join(' · ') || undefined
              : 'None announced within three months'
          }
        />
        <Stat
          info="epsSurprise"
          label="Last EPS surprise"
          value={signedPercent(lastQuarter?.surprise_percent)}
          className={tone(lastQuarter?.surprise_percent)}
          detail={
            lastQuarter &&
            `${quarterName(lastQuarter)}: ${number(lastQuarter.actual)} vs ${number(lastQuarter.estimate)}`
          }
        />
        <Stat
          info="insiders"
          label="Insiders, 90 days"
          value={
            insiders
              ? `${insiders.net_value < 0 ? '−' : ''}${compact(Math.abs(insiders.net_value))}`
              : '—'
          }
          className={insiders ? tone(insiders.net_value) : undefined}
          detail={
            insiders
              ? `Net · ${count(insiders.purchases, 'purchase')}, ${count(insiders.sales, 'sale')}`
              : 'Unavailable'
          }
        />
        <Stat
          info="insiderSentiment"
          label="Insider sentiment"
          value={sentiment == null ? '—' : number(sentiment, 0)}
          className={tone(sentiment)}
          detail={
            sentiment == null
              ? 'No insider trading this year'
              : `MSPR, last ${count(recentSentiment.length, 'month')} · −100 to 100`
          }
        />
      </div>
      <div className="grid-2">
        <div>
          <h3>Analyst ratings</h3>
          {signals.recommendations.length ? (
            <div className="table-wrap">
              <RatingsTable months={signals.recommendations} />
            </div>
          ) : (
            <p className="disclaimer">No analysts cover this stock.</p>
          )}
        </div>
        <div>
          <h3>Earnings per share</h3>
          {signals.earnings.length ? (
            <div className="table-wrap">
              <EarningsTable quarters={signals.earnings} />
            </div>
          ) : (
            <p className="disclaimer">No earnings history.</p>
          )}
        </div>
      </div>
      {signals.insider_trades.length > 0 && (
        <div>
          <h3>Insider trades</h3>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Insider</th>
                  <th className="num">Shares</th>
                  <th className="num">Price</th>
                  <th className="num">Value</th>
                </tr>
              </thead>
              <tbody>
                {signals.insider_trades.map((trade, index) => (
                  <tr key={`${trade.name}-${trade.transaction_date}-${index}`}>
                    <td>{shortDate(trade.transaction_date)}</td>
                    <td>
                      {trade.name}{' '}
                      <span className={`badge ${trade.shares > 0 ? 'good' : ''}`}>
                        {trade.shares > 0 ? 'Bought' : 'Sold'}
                      </span>
                    </td>
                    <td className="num">{number(Math.abs(trade.shares), 0)}</td>
                    <td className="num">{money(trade.price, currency)}</td>
                    <td className="num">
                      {money(trade.value == null ? null : Math.abs(trade.value), currency, 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function RatingsTable({ months }: { months: Month[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Month</th>
          <th className="num">Strong buy</th>
          <th className="num">Buy</th>
          <th className="num">Hold</th>
          <th className="num">Sell</th>
          <th className="num">Strong sell</th>
        </tr>
      </thead>
      <tbody>
        {months.map((month) => (
          <tr key={month.period}>
            <td>{monthName(month.period)}</td>
            <td className="num">{month.strong_buy}</td>
            <td className="num">{month.buy}</td>
            <td className="num">{month.hold}</td>
            <td className="num">{month.sell}</td>
            <td className="num">{month.strong_sell}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function EarningsTable({ quarters }: { quarters: Quarter[] }) {
  return (
    <table className="data">
      <thead>
        <tr>
          <th>Quarter</th>
          <th className="num">Actual</th>
          <th className="num">Estimate</th>
          <th className="num">Surprise</th>
        </tr>
      </thead>
      <tbody>
        {quarters.map((quarter) => (
          <tr key={quarter.period}>
            <td>{quarterName(quarter)}</td>
            <td className="num">{number(quarter.actual)}</td>
            <td className="num">{number(quarter.estimate)}</td>
            <td className={`num ${tone(quarter.surprise_percent) ?? ''}`}>
              {signedPercent(quarter.surprise_percent)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
