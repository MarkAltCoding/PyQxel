import { useEffect, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from '../auth/useAuth'
import { startSavedState, useSavedStateReady } from '../lib/savedState'

/**
 * Sends visitors without an API key to sign in, then back here: the same page, with its
 * query (such as which backtest or symbol) and the section its link named. Once signed
 * in, it first brings in what the account has saved (drafts, screens, answers, and
 * choices), so every page opens with them, in any browser.
 */
export function RequireKey({ children }: { children: ReactNode }) {
  const { apiKey } = useAuth()
  const { pathname, search, hash } = useLocation()
  const ready = useSavedStateReady()
  useEffect(() => {
    if (apiKey) void startSavedState()
  }, [apiKey])
  if (!apiKey) {
    return <Navigate to="/signin" replace state={{ from: `${pathname}${search}${hash}` }} />
  }
  if (!ready) {
    return (
      <div className="empty" role="status" aria-label="Loading your saved settings">
        <div className="spinner" style={{ margin: '0 auto' }} />
      </div>
    )
  }
  return children
}
