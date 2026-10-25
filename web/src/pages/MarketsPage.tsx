import { useState } from 'react'
import { Link } from 'react-router'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getMarketCalendar, getMarketNews } from '../api/endpoints'
import type { MarketCalendar, MarketNewsCategory } from '../api/types'
import { calendarRows, type CalendarRow } from '../lib/calendar'
import { ErrorNotice, Notice } from '../components/Notice'
import { Segmented } from '../components/Segmented'
import { timeAgo } from '../lib/format'

const WINDOWS = [
  { value: '14', label: '2 weeks' },
  { value: '30', label: '30 days' },
  { value: '60', label: '60 days' },
] as const
type Window = (typeof WINDOWS)[number]['value']

const KINDS = [
  { value: 'all', label: 'All' },
  { value: 'fomc', label: 'FOMC' },
  { value: 'earnings', label: 'Earnings' },
  { value: 'ipo', label: 'IPOs' },
  { value: 'holiday', label: 'Holidays' },
] as const
type Kind = (typeof KINDS)[number]['value']

const SIZES = [
  { value: '50000000000', label: '$50B+' },
  { value: '10000000000', label: '$10B+' },
] as const
type Size = (typeof SIZES)[number]['value']

const CATEGORIES = [
  { value: 'general', label: 'Markets' },
  { value: 'merger', label: 'Mergers' },
  { value: 'forex', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
] as const

const KIND_LABELS: Record<CalendarRow['kind'], string> = {
  fomc: 'FOMC',
  earnings: 'Earnings',
  ipo: 'IPO',
  holiday: 'Holiday',
}

const FIRST_NEWS = 15

const dayName = (day: string) =>
  new Date(`${day}T00:00:00`).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })

export function MarketsPage() {
  const [window, setWindow] = useState<Window>('30')
  const [kind, setKind] = useState<Kind>('all')
  const [size, setSize] = useState<Size>('50000000000')
  const [category, setCategory] = useState<MarketNewsCategory>('general')
  const calendar = useQuery({
    queryKey: ['market-calendar', window, size],
    queryFn: () => getMarketCalendar(Number(window), Number(size)),
    placeholderData: keepPreviousData,
  })
  const status = calendar.data?.status

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Markets</h1>
          <div className="subtitle">
            {status
              ? `US markets ${status.is_open ? `open · ${status.session ?? 'trading'}` : 'closed'}${status.holiday ? ` · ${status.holiday}` : ''}`
              : 'Market-moving dates and the latest market news'}
          </div>
        </div>
      </header>
      <section className="card" aria-busy={calendar.isLoading || calendar.isPlaceholderData}>
        <div className="card-header">
          <div>
            <h2>Calendar</h2>
            <div className="subtitle">
              FOMC meetings from the Federal Reserve; earnings of companies above the market cap
              chosen, IPOs and market holidays from Finnhub
            </div>
          </div>
          <div className="filter-row">
            <Segmented label="Events" value={kind} options={KINDS} onChange={setKind} />
            {(kind === 'all' || kind === 'earnings') && (
              <Segmented
                label="Companies reporting, by market cap"
                value={size}
                options={SIZES}
                onChange={setSize}
              />
            )}
            <Segmented label="Window" value={window} options={WINDOWS} onChange={setWindow} />
          </div>
        </div>
        {calendar.error !== null && <ErrorNotice error={calendar.error} />}
        {calendar.data?.notice && <Notice tone="warning">{calendar.data.notice}</Notice>}
        {calendar.data ? (
          <CalendarList calendar={calendar.data} kind={kind} />
        ) : (
          calendar.isLoading && (
            <div className="empty">
              <div className="spinner" style={{ margin: '0 auto' }} />
            </div>
          )
        )}
      </section>
      <MarketNewsCard category={category} onCategory={setCategory} />
    </div>
  )
}

function CalendarList({ calendar, kind }: { calendar: MarketCalendar; kind: Kind }) {
  const rows = calendarRows(calendar).filter((row) => kind === 'all' || row.kind === kind)
  if (rows.length === 0) {
    return <div className="empty">Nothing on the calendar in this window.</div>
  }
  const days = [...new Set(rows.map((row) => row.date))]
  return (
    <div className={`calendar${kind === 'all' ? '' : ' filtered'}`}>
      {days.map((day) => (
        <div key={day} className="calendar-day">
          <h3>{dayName(day)}</h3>
          <ul>
            {rows
              .filter((row) => row.date === day)
              .map((row, index) => (
                <li key={`${row.kind}-${row.title}-${index}`}>
                  <span className={`badge calendar-kind ${row.kind}`}>{KIND_LABELS[row.kind]}</span>
                  <span className="calendar-title">
                    {row.symbol && row.kind === 'earnings' ? (
                      <Link to={`/stocks/${encodeURIComponent(row.symbol)}`}>{row.title}</Link>
                    ) : (
                      row.title
                    )}
                    {row.detail && <span className="calendar-detail"> · {row.detail}</span>}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  )
}

function MarketNewsCard({
  category,
  onCategory,
}: {
  category: MarketNewsCategory
  onCategory: (category: MarketNewsCategory) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const news = useQuery({
    queryKey: ['market-news', category],
    queryFn: () => getMarketNews(category),
    placeholderData: keepPreviousData,
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  })
  const noKey = news.error instanceof ApiError && news.error.status === 403
  const articles = news.data?.articles ?? []
  const shown = expanded ? articles : articles.slice(0, FIRST_NEWS)
  return (
    <section className="card" aria-busy={news.isLoading || news.isPlaceholderData}>
      <div className="card-header">
        <div>
          <h2>Market news</h2>
          <div className="subtitle">The latest market-wide headlines, from Finnhub</div>
        </div>
        <Segmented
          label="News category"
          value={category}
          options={CATEGORIES}
          onChange={(next) => {
            setExpanded(false)
            onCategory(next)
          }}
        />
      </div>
      {noKey ? (
        <div className="empty">
          Add a Finnhub key in <Link to="/settings">Settings</Link> to see market news.
        </div>
      ) : news.error ? (
        <ErrorNotice error={news.error} />
      ) : news.isLoading ? (
        <div className="empty">
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : articles.length === 0 ? (
        <div className="empty">No news in the last week.</div>
      ) : (
        <div className={`stack${news.isPlaceholderData ? ' refreshing' : ''}`} style={{ gap: 12 }}>
          <ul className="news-list">
            {shown.map((article) => (
              <li key={article.url}>
                <a href={article.url} target="_blank" rel="noopener noreferrer">
                  {article.headline}
                </a>
                <div className="news-meta">
                  {article.source} ·{' '}
                  <time dateTime={article.published_at} title={article.published_at}>
                    {timeAgo(article.published_at)}
                  </time>
                </div>
                {article.summary && <p className="news-summary">{article.summary}</p>}
              </li>
            ))}
          </ul>
          {articles.length > FIRST_NEWS && (
            <div>
              <button type="button" className="button" onClick={() => setExpanded((o) => !o)}>
                {expanded ? 'Show fewer' : `Show ${articles.length - FIRST_NEWS} more`}
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
