import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import { getNews } from '../api/endpoints'
import type { NewsArticle } from '../api/types'
import { ChartCard } from '../charts/ChartCard'
import { NewsChart, NewsTable } from '../charts/NewsChart'
import { number, shortDate, timeAgo } from '../lib/format'
import { Notice } from './Notice'
import { Stat } from './Stat'

const FIRST_ARTICLES = 8

/**
 * The last two weeks of news about a stock, from Finnhub on the account's own key: how
 * much coverage it drew, from whom, and the newest headlines.
 */
export function NewsCard({ symbol }: { symbol: string }) {
  const news = useQuery({
    queryKey: ['news', symbol],
    queryFn: () => getNews(symbol),
    retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
  })
  const [expanded, setExpanded] = useState(false)
  const noKey = news.error instanceof ApiError && news.error.status === 403
  const data = news.data
  const overview = data?.overview
  const shown = expanded ? data?.articles : data?.articles.slice(0, FIRST_ARTICLES)

  return (
    <ChartCard
      title="News"
      subtitle={
        overview
          ? `Last two weeks from Finnhub · ${overview.total_articles} articles`
          : 'Last two weeks of company news'
      }
      loading={news.isLoading}
      error={noKey ? undefined : (news.error ?? undefined)}
      chart={overview && overview.total_articles > 0 && <NewsChart daily={overview.daily} />}
      table={overview && <NewsTable daily={overview.daily} />}
      empty={
        noKey ? (
          <>
            Add a Finnhub key in <Link to="/settings">Settings</Link> to see company news here and
            give it to AI reports. The free plan is enough.
          </>
        ) : (
          `No news about ${symbol} in the last two weeks.`
        )
      }
      footer={
        data &&
        overview && (
          <div className="stack" style={{ gap: 14, marginTop: overview.total_articles ? 14 : 0 }}>
            {data.notice && <Notice tone="warning">{data.notice}</Notice>}
            {overview.total_articles > 0 && (
              <>
                <div className="stats">
                  <Stat label="Last 24 hours" value={overview.last_day_articles} />
                  <Stat label="Last 7 days" value={overview.last_week_articles} />
                  <Stat
                    label="Per day"
                    value={number(overview.daily_average, 1)}
                    detail="Two-week average"
                  />
                  <Stat
                    label="Busiest day"
                    value={
                      overview.busiest_day
                        ? shortDate(overview.busiest_day.day).replace(/, \d{4}$/, '')
                        : '—'
                    }
                    detail={overview.busiest_day && `${overview.busiest_day.articles} articles`}
                  />
                </div>
                <div className="filter-row" aria-label="Top publishers">
                  {overview.sources.map((source) => (
                    <span key={source.source} className="badge">
                      {source.source} · {source.articles}
                    </span>
                  ))}
                </div>
                <ul className="news-list">
                  {shown?.map((article) => (
                    <Article key={article.url} article={article} />
                  ))}
                </ul>
                {data.articles.length > FIRST_ARTICLES && (
                  <div>
                    <button
                      type="button"
                      className="button"
                      onClick={() => setExpanded((open) => !open)}
                    >
                      {expanded
                        ? 'Show fewer'
                        : `Show ${data.articles.length - FIRST_ARTICLES} more`}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )
      }
    />
  )
}

function Article({ article }: { article: NewsArticle }) {
  return (
    <li>
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
  )
}
