import { useState } from 'react'
import { Link } from 'react-router'
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getMarketCalendar, getMarketNews } from '../api/endpoints'
import type { MarketCalendar, MarketNewsCategory, NewsArticle } from '../api/types'
import { calendarRows, type CalendarRow } from '../lib/calendar'
import { MultiToggle } from '../components/MultiToggle'
import { ErrorNotice, Notice } from '../components/Notice'
import { Pager } from '../components/Pager'
import { PresetOrCustom } from '../components/PresetOrCustom'
import { Segmented } from '../components/Segmented'
import { timeAgo } from '../lib/format'
import { safeUrl } from '../lib/safeUrl'
import { PAGE_SIZES, storedPageSize } from '../lib/paging'
import { writeStored } from '../lib/storage'

const WINDOWS = [
  { value: 14, label: '2 weeks' },
  { value: 30, label: '30 days' },
  { value: 60, label: '60 days' },
] as const

/** Longest window the calendar may cover, as the API allows. */
const MAX_DAYS = 90

type Kind = CalendarRow['kind']

const KINDS: { value: Kind; label: string }[] = [
  { value: 'fomc', label: 'FOMC' },
  { value: 'earnings', label: 'Earnings' },
  { value: 'ipo', label: 'IPOs' },
  { value: 'holiday', label: 'Holidays' },
]

const ALL_KINDS = KINDS.map((kind) => kind.value)

const SIZES = [
  { value: 10e9, label: '$10B+' },
  { value: 50e9, label: '$50B+' },
  { value: 100e9, label: '$100B+' },
  { value: 500e9, label: '$500B+' },
] as const

/** Range of a custom market cap, in billions of dollars, as the API allows. */
const MIN_CAP_BILLIONS = 1
const MAX_CAP_BILLIONS = 5000

const CATEGORIES: { value: MarketNewsCategory; label: string }[] = [
  { value: 'general', label: 'Markets' },
  { value: 'merger', label: 'Mergers' },
  { value: 'forex', label: 'Forex' },
  { value: 'crypto', label: 'Crypto' },
]

type NewsOrder = 'importance' | 'newest'

const ORDERS: { value: NewsOrder; label: string }[] = [
  { value: 'importance', label: 'Most important' },
  { value: 'newest', label: 'Newest' },
]

const PAGE_SIZE_KEY = 'pyqxel.marketNewsPerPage'

const KIND_LABELS: Record<CalendarRow['kind'], string> = {
  fomc: 'FOMC',
  earnings: 'Earnings',
  ipo: 'IPO',
  holiday: 'Holiday',
}

const dayName = (day: string) =>
  new Date(`${day}T00:00:00`).toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })

export function MarketsPage() {
  const [days, setDays] = useState(30)
  const [kinds, setKinds] = useState<Kind[]>(ALL_KINDS)
  const [size, setSize] = useState(50e9)
  const [categories, setCategories] = useState<MarketNewsCategory[]>(['general'])
  const calendar = useQuery({
    queryKey: ['market-calendar', days, size],
    queryFn: () => getMarketCalendar(days, size),
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
            <MultiToggle label="Events" values={kinds} options={KINDS} onChange={setKinds} />
            {kinds.includes('earnings') && (
              <PresetOrCustom
                label="Companies reporting, by market cap"
                presets={SIZES}
                value={size}
                onChange={setSize}
                min={MIN_CAP_BILLIONS}
                max={MAX_CAP_BILLIONS}
                unit="$ billions"
                scale={1e9}
              />
            )}
            <PresetOrCustom
              label="Window"
              presets={WINDOWS}
              value={days}
              onChange={setDays}
              min={1}
              max={MAX_DAYS}
              unit="days"
            />
          </div>
        </div>
        {calendar.error !== null && <ErrorNotice error={calendar.error} />}
        {calendar.data?.notice && <Notice tone="warning">{calendar.data.notice}</Notice>}
        {calendar.data ? (
          <CalendarList calendar={calendar.data} kinds={kinds} />
        ) : (
          calendar.isLoading && (
            <div className="empty">
              <div className="spinner" style={{ margin: '0 auto' }} />
            </div>
          )
        )}
      </section>
      <MarketNewsCard categories={categories} onCategories={setCategories} />
    </div>
  )
}

function CalendarList({ calendar, kinds }: { calendar: MarketCalendar; kinds: Kind[] }) {
  if (kinds.length === 0) {
    return <div className="empty">Choose the kinds of event to show.</div>
  }
  const rows = calendarRows(calendar).filter((row) => kinds.includes(row.kind))
  if (rows.length === 0) {
    return <div className="empty">Nothing on the calendar in this window.</div>
  }
  const days = [...new Set(rows.map((row) => row.date))]
  // With a single kind shown, its badge on every row says nothing.
  return (
    <div className={`calendar${kinds.length === 1 ? ' filtered' : ''}`}>
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

/** The articles of every category asked for, each once, in `order`. */
function mergeNews(lists: NewsArticle[][], order: NewsOrder): NewsArticle[] {
  const byUrl = new Map<string, NewsArticle>()
  for (const article of lists.flat()) if (!byUrl.has(article.url)) byUrl.set(article.url, article)
  const newest = (a: NewsArticle, b: NewsArticle) => b.published_at.localeCompare(a.published_at)
  return [...byUrl.values()].sort((a, b) =>
    order === 'newest' ? newest(a, b) : (b.importance ?? 0) - (a.importance ?? 0) || newest(a, b),
  )
}

function MarketNewsCard({
  categories,
  onCategories,
}: {
  categories: MarketNewsCategory[]
  onCategories: (categories: MarketNewsCategory[]) => void
}) {
  const [order, setOrder] = useState<NewsOrder>('importance')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(() => storedPageSize(PAGE_SIZE_KEY))
  const results = useQueries({
    queries: categories.map((category) => ({
      queryKey: ['market-news', category],
      queryFn: () => getMarketNews(category),
      placeholderData: keepPreviousData,
      retry: (count: number, error: Error) =>
        !(error instanceof ApiError && error.status < 500) && count < 2,
    })),
  })
  const errors = results.map((result) => result.error).filter((error) => error !== null)
  const noKey = errors.some((error) => error instanceof ApiError && error.status === 403)
  const loading = results.some((result) => result.isLoading)
  const refreshing = results.some((result) => result.isPlaceholderData)
  const articles = mergeNews(
    results.map((result) => result.data?.articles ?? []),
    order,
  )
  const pages = Math.max(1, Math.ceil(articles.length / pageSize))
  const current = Math.min(page, pages)
  const shown = articles.slice((current - 1) * pageSize, current * pageSize)
  const failedAll = errors.length > 0 && errors.length === results.length
  return (
    <section className="card" aria-busy={loading || refreshing}>
      <div className="card-header">
        <div>
          <h2>Market news</h2>
          <div className="subtitle">
            The latest market-wide headlines, from Finnhub
            {order === 'importance' ? ', the most market-moving first' : ', newest first'}
          </div>
        </div>
        <div className="filter-row">
          <MultiToggle
            label="News categories"
            values={categories}
            options={CATEGORIES}
            onChange={(next) => {
              setPage(1)
              onCategories(next)
            }}
          />
          <Segmented
            label="Order"
            value={order}
            options={ORDERS}
            onChange={(next) => {
              setPage(1)
              setOrder(next)
            }}
          />
        </div>
      </div>
      {categories.length === 0 ? (
        <div className="empty">Choose the kinds of news to show.</div>
      ) : noKey ? (
        <div className="empty">
          Add a Finnhub key in <Link to="/settings">Settings</Link> to see market news.
        </div>
      ) : failedAll ? (
        <ErrorNotice error={errors[0]} />
      ) : loading && articles.length === 0 ? (
        <div className="empty">
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : articles.length === 0 ? (
        <div className="empty">No news in the last week.</div>
      ) : (
        <div className={`stack${refreshing ? ' refreshing' : ''}`} style={{ gap: 12 }}>
          {errors.length > 0 && (
            <Notice tone="warning">Some categories could not be loaded and are left out.</Notice>
          )}
          <ul className="news-list">
            {shown.map((article) => (
              <li key={article.url}>
                <a href={safeUrl(article.url)} target="_blank" rel="noopener noreferrer">
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
          <Pager
            page={current}
            pageSize={pageSize}
            total={articles.length}
            sizes={PAGE_SIZES}
            noun="headlines"
            onPage={setPage}
            onPageSize={(size) => {
              writeStored(PAGE_SIZE_KEY, String(size))
              setPageSize(size)
              setPage(1)
            }}
          />
        </div>
      )}
    </section>
  )
}
