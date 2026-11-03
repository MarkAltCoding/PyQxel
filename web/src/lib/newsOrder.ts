// How a news list is ordered: the headlines that look to matter most first, or the newest
// first. The server scores each headline's `importance` (app/stats/markets.py for market
// news, app/stats/news.py for a company's).

import type { NewsArticle } from '../api/types'

export type NewsOrder = 'importance' | 'newest'

export const NEWS_ORDERS: { value: NewsOrder; label: string }[] = [
  { value: 'importance', label: 'Most important' },
  { value: 'newest', label: 'Newest' },
]

const newest = (a: NewsArticle, b: NewsArticle) => b.published_at.localeCompare(a.published_at)

/** `articles` in `order`; ties in importance go newest first. */
export function sortNews(articles: NewsArticle[], order: NewsOrder): NewsArticle[] {
  return [...articles].sort((a, b) =>
    order === 'newest' ? newest(a, b) : (b.importance ?? 0) - (a.importance ?? 0) || newest(a, b),
  )
}
