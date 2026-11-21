// When a failed query is tried again: a server failure twice, and a key that has used its
// requests for the minute once, after the wait the server names, so news that waited on
// Finnhub's per-minute limit loads by itself. Other client errors (a bad symbol, a missing
// key, a plan that does not cover the data) will not fix themselves.

import { ApiError } from '../api/client'

/** The longest wait, in seconds, a query sits out before trying again by itself. */
const LONGEST_WAIT_SECONDS = 75

export function retryQuery(failures: number, error: Error): boolean {
  if (error instanceof ApiError && error.status === 429) {
    return failures < 1 && error.retryAfter !== null && error.retryAfter <= LONGEST_WAIT_SECONDS
  }
  return failures < 2 && !(error instanceof ApiError && error.status < 500)
}

export function retryDelay(failures: number, error: Error): number {
  if (error instanceof ApiError && error.status === 429 && error.retryAfter !== null) {
    return error.retryAfter * 1000 + 500
  }
  return Math.min(1000 * 2 ** failures, 30_000)
}
