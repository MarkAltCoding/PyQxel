import { useEffect, useState } from 'react'
import { describeHref, leavingHref } from '../lib/externalLinks'
import { isNativeApp } from '../lib/native'
import { OpenInBrowserDialog } from './OpenInBrowserDialog'

/**
 * In the native app, catches every tap on a link that leaves PyQxel, asks first, and then
 * opens it in the system browser (or mail, or the phone). A web view cannot open such a
 * link itself, so without this a tap did nothing. In a browser it does nothing, and links
 * open in a new tab as usual.
 */
export function ExternalLinkGuard() {
  const [pending, setPending] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (!isNativeApp()) return
    const onClick = (event: MouseEvent) => {
      const href = leavingHref(event.target, window.location.origin)
      if (!href) return
      event.preventDefault()
      event.stopPropagation()
      setFailed(null)
      setPending(href)
    }
    // Captured before any other handler, so no link leaves without asking.
    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [])

  if (!pending) return null
  const close = () => setPending(null)
  const web = pending.startsWith('http')
  return (
    <OpenInBrowserDialog
      title={
        web ? `Open ${describeHref(pending)} in your browser?` : `Open ${describeHref(pending)}?`
      }
      continueLabel={web ? 'Open in browser' : 'Open'}
      onCancel={close}
      onContinue={() => {
        import('@tauri-apps/plugin-opener')
          .then(({ openUrl }) => openUrl(pending))
          .then(close)
          .catch(() => setFailed(pending))
      }}
    >
      <p style={{ margin: 0, overflowWrap: 'anywhere' }}>
        {web
          ? 'This link leaves PyQxel and opens in your web browser. PyQxel stays open behind it.'
          : 'This opens another app.'}{' '}
        <span style={{ color: 'var(--text-muted)' }}>{pending}</span>
      </p>
      {failed && (
        <p role="alert" style={{ margin: '8px 0 0' }}>
          It could not be opened. Copy the address above into your browser instead.
        </p>
      )}
    </OpenInBrowserDialog>
  )
}
