import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { ApiError } from '../api/client'

export function Notice({ children, tone }: { children: ReactNode; tone?: 'error' | 'warning' }) {
  const icon = tone === 'error' ? '!' : tone === 'warning' ? '!' : 'i'
  return (
    <div className={`notice${tone ? ` ${tone}` : ''}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span className="icon" aria-hidden="true">
        {icon}
      </span>
      <div>{children}</div>
    </div>
  )
}

/** Explains a failed request, with the fix when there is one. */
export function ErrorNotice({ error }: { error: unknown }) {
  if (!(error instanceof ApiError)) {
    const message = error instanceof Error ? error.message : String(error)
    return <Notice tone="error">Could not reach PyQxel: {message}</Notice>
  }
  let hint: ReactNode = null
  if (error.status === 403 || error.status === 402) {
    hint = (
      <>
        {' '}
        Your provider keys are in <Link to="/settings">Settings</Link>.
      </>
    )
  } else if (error.status === 429 && error.retryAfter) {
    hint = ` Try again in ${formatWait(error.retryAfter)}.`
  }
  return (
    <Notice tone="error">
      {error.message}
      {hint}
    </Notice>
  )
}

function formatWait(seconds: number): string {
  if (seconds < 90) return `${Math.ceil(seconds)} seconds`
  if (seconds < 5400) return `${Math.ceil(seconds / 60)} minutes`
  return `${Math.ceil(seconds / 3600)} hours`
}
