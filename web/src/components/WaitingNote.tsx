import { useEffect, useState } from 'react'

/** Seconds with nothing from Claude yet after which the wait is explained. */
const QUIET_SECONDS = 20

/**
 * While a report is still gathering its data and Claude has not begun, says that it may be
 * waiting out Finnhub's per-minute limit, as long as the person chose to wait, with how long
 * so far. The data is gathered before Claude's answer starts streaming, so the quiet is the
 * sign; nothing shows when the person chose not to wait.
 */
export function WaitingNote({ wait, quiet }: { wait: number; quiet: boolean }) {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    if (!quiet) return
    const started = Date.now()
    const timer = window.setInterval(
      () => setSeconds(Math.round((Date.now() - started) / 1000)),
      1000,
    )
    return () => window.clearInterval(timer)
  }, [quiet])
  if (!quiet || wait <= 0 || seconds < QUIET_SECONDS) return null
  const minutes = Math.floor(seconds / 60)
  const so = minutes > 0 ? `${minutes} min ${seconds % 60} s` : `${seconds} s`
  return (
    <p className="disclaimer" style={{ margin: 0 }} role="status">
      Still gathering data ({so} so far): most likely waiting for Finnhub’s limit of 60 requests a
      minute, for up to the {Math.round(wait / 60)} {wait >= 120 ? 'minutes' : 'minute'} you chose
      to wait. Claude starts writing once the data is in.
    </p>
  )
}
