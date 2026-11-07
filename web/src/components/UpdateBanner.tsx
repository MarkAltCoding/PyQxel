import { useEffect, useState } from 'react'
import {
  CHECK_EVERY_MS,
  dismissedUpdate,
  dismissUpdate,
  findUpdate,
  updatesEnabled,
  type AvailableUpdate,
} from '../lib/appUpdates'
import { percent } from '../lib/format'

const RELEASES = 'https://github.com/MarkAltCoding/PyQxel/releases/latest'

/**
 * In the desktop app, says when a newer version is out and installs it with one click,
 * restarting into it. Looks on opening and every few hours; "Later" hides that version
 * until a newer one comes. Shows nothing in a browser or on a phone.
 */
export function UpdateBanner() {
  const [update, setUpdate] = useState<AvailableUpdate | null>(null)
  const [progress, setProgress] = useState<number | null | undefined>(undefined)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!updatesEnabled()) return
    let stopped = false
    const look = () =>
      void findUpdate().then((found) => {
        if (!stopped && found && !dismissedUpdate(found.version)) setUpdate(found)
      })
    // A moment after opening, so the first pages load before anything else is fetched.
    const first = window.setTimeout(look, 5000)
    const timer = window.setInterval(look, CHECK_EVERY_MS)
    return () => {
      stopped = true
      window.clearTimeout(first)
      window.clearInterval(timer)
    }
  }, [])

  if (!update) return null
  const installing = progress !== undefined && !failed
  const install = () => {
    setFailed(false)
    setProgress(null)
    update.install(setProgress).catch(() => setFailed(true))
  }

  return (
    <aside className="update-banner card" role="status" aria-live="polite">
      <div className="stack" style={{ gap: 4 }}>
        <strong>PyQxel {update.version} is available</strong>
        {update.notes && !installing && !failed && (
          <span className="update-notes">{update.notes}</span>
        )}
        {installing && (
          <span>
            {progress === null || progress === undefined
              ? 'Downloading…'
              : `Downloading… ${percent(progress, 0)}`}{' '}
            PyQxel restarts when it is done; your sign-in and keys stay.
          </span>
        )}
        {failed && (
          <span role="alert">
            The update could not be installed. You can{' '}
            <a href={RELEASES} target="_blank" rel="noopener noreferrer">
              download it
            </a>{' '}
            instead.
          </span>
        )}
      </div>
      <div className="update-actions">
        {!installing && (
          <button
            type="button"
            className="button ghost small"
            onClick={() => {
              dismissUpdate(update.version)
              setUpdate(null)
            }}
          >
            Later
          </button>
        )}
        <button
          type="button"
          className="button primary small"
          onClick={install}
          disabled={installing}
        >
          {failed ? 'Try again' : installing ? 'Updating…' : 'Update and restart'}
        </button>
      </div>
    </aside>
  )
}
