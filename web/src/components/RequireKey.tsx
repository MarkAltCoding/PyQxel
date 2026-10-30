import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from '../auth/useAuth'

/** Sends visitors without an API key to sign in, then back here: the same page, with its
 * query (such as which backtest or symbol) and the section its link named. */
export function RequireKey({ children }: { children: ReactNode }) {
  const { apiKey } = useAuth()
  const { pathname, search, hash } = useLocation()
  if (!apiKey) {
    return <Navigate to="/signin" replace state={{ from: `${pathname}${search}${hash}` }} />
  }
  return children
}
