import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuth } from '../auth/useAuth'

/** Sends visitors without an API key to sign in, then back here. */
export function RequireKey({ children }: { children: ReactNode }) {
  const { apiKey } = useAuth()
  const location = useLocation()
  if (!apiKey) return <Navigate to="/signin" replace state={{ from: location.pathname }} />
  return children
}
