import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getAlternative } from '../api/endpoints'
import type { AlternativeData } from '../api/types'
import { compact, shortDate } from '../lib/format'
import { ErrorNotice, Notice } from './Notice'
import { Stat } from './Stat'
import { safeUrl } from '../lib/safeUrl'

const FORM_NAMES: Record<string, string> = {
  '8-K': 'material events',
  '4': 'insider trades',
  '144': 'planned insider sales',
  '10-Q': 'quarterly report',
  '10-K': 'annual report',
  '3': 'new insiders',
  'SC 13G': 'large holders',
  'SC 13G/A': 'large holders',
}

/**
 * A company's lobbying, federal contracts, patents and SEC filings, from public records
 * Finnhub relays on the account's own key.
 */
export function AlternativeCard({ symbol }: { symbol: string }) {
  const alternative = useQuery({
    queryKey: ['alternative', symbol],
    queryFn: () => getAlternative(symbol),
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  })
  const noKey = alternative.error instanceof ApiError && alternative.error.status === 403
  return (
    <section id="alternative-data" className="card" aria-busy={alternative.isLoading}>
      <div className="card-header">
        <div>
          <h2>Lobbying, government contracts and patents</h2>
          <div className="subtitle">
            Federal lobbying disclosures, USAspending.gov awards, USPTO patents and SEC filings,
            from Finnhub
          </div>
        </div>
      </div>
      {noKey ? (
        <div className="empty">
          Add a Finnhub key in <Link to="/settings">Settings</Link> to see lobbying, government
          contracts, patents and recent filings.
        </div>
      ) : alternative.error ? (
        <ErrorNotice error={alternative.error} />
      ) : !alternative.data ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <Body data={alternative.data} />
      )}
    </section>
  )
}

function Body({ data }: { data: AlternativeData }) {
  const lobbyingThisYear = data.lobbying_years[0]
  const filings = Object.values(data.filing_counts ?? {}).reduce((sum, n) => sum + n, 0)
  return (
    <div className="stack" style={{ gap: 16 }}>
      {data.notice && <Notice tone="warning">{data.notice}</Notice>}
      <div className="stats">
        <Stat
          info="lobbying"
          label="Lobbying"
          value={lobbyingThisYear ? compact(lobbyingThisYear.amount) : '—'}
          detail={
            lobbyingThisYear
              ? `${lobbyingThisYear.year}, ${lobbyingThisYear.filings} disclosures${String(lobbyingThisYear.year) === data.as_of.slice(0, 4) ? ' so far' : ''}`
              : 'None disclosed since last year'
          }
        />
        <Stat
          info="federalAwards"
          label="Federal awards, 1 year"
          value={data.contracts_total == null ? '—' : compact(data.contracts_total)}
          detail={
            data.contract_agencies[0]
              ? `Most from ${data.contract_agencies[0].agency}`
              : data.contracts_total == null
                ? 'Unavailable'
                : 'None'
          }
        />
        <Stat
          info="patents"
          label="Patents, 1 year"
          value={data.patents_count == null ? '—' : data.patents_count}
          detail="Published by the USPTO"
        />
        <Stat
          info="secFilings"
          label="SEC filings, 90 days"
          value={filings}
          detail={formSummary(data)}
        />
      </div>
      <div className="grid-2">
        <div>
          <h3>Largest federal awards</h3>
          {data.contracts.length ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Agency</th>
                    <th className="num">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {data.contracts.map((award, index) => (
                    <tr key={`${award.action_date}-${index}`}>
                      <td>{shortDate(award.action_date)}</td>
                      <td className="award">
                        {award.sub_agency ?? award.agency ?? '—'}
                        {award.description && (
                          <div className="disclaimer">{describe(award.description)}</div>
                        )}
                      </td>
                      <td className="num">{compact(award.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="disclaimer">No federal awards in the last year.</p>
          )}
        </div>
        <div>
          <h3>Lobbying by year</h3>
          {data.lobbying_years.length ? (
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th className="num">Disclosures</th>
                    <th className="num">Spent</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lobbying_years.map((year) => (
                    <tr key={year.year}>
                      <td>{year.year}</td>
                      <td className="num">{year.filings}</td>
                      <td className="num">{compact(year.amount)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="disclaimer">No lobbying disclosed since last year.</p>
          )}
        </div>
      </div>
      <div className="grid-2">
        <div>
          <h3>Newest patents</h3>
          {data.patents.length ? (
            <ul className="news-list">
              {data.patents.map((patent, index) => (
                <li key={patent.number ?? index}>
                  {patent.description}
                  <div className="news-meta">
                    {[patent.number, patent.status, shortDate(patent.publication_date)]
                      .filter(Boolean)
                      .join(' · ')}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="disclaimer">None published in the last year.</p>
          )}
        </div>
        <div>
          <h3>Recent SEC filings</h3>
          {data.filings.length ? (
            <ul className="news-list">
              {data.filings.map((filing, index) => (
                <li key={`${filing.form}-${filing.filed}-${index}`}>
                  {filing.url ? (
                    <a href={safeUrl(filing.url)} target="_blank" rel="noopener noreferrer">
                      Form {filing.form}
                    </a>
                  ) : (
                    `Form ${filing.form}`
                  )}
                  <div className="news-meta">
                    {[FORM_NAMES[filing.form], shortDate(filing.filed)].filter(Boolean).join(' · ')}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="disclaimer">No filings in the last 90 days.</p>
          )}
        </div>
      </div>
    </div>
  )
}

function formSummary(data: AlternativeData): string | undefined {
  const counts = Object.entries(data.filing_counts ?? {}).slice(0, 3)
  return counts.length ? counts.map(([form, n]) => `${n} × ${form}`).join(', ') : undefined
}

/** An award's description, which agencies often file in capitals, shortened to a line. */
function describe(text: string): string {
  const readable = text === text.toUpperCase() ? text.charAt(0) + text.slice(1).toLowerCase() : text
  return readable.length > 90 ? `${readable.slice(0, 90).replace(/\s+\S*$/, '')}…` : readable
}
