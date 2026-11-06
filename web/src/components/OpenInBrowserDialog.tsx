import type { ReactNode } from 'react'

/**
 * Asks before the native app hands the person to another app (the browser, mail, or the
 * phone), as iOS does when an app opens a web page, so leaving is never a surprise.
 */
export function OpenInBrowserDialog({
  title,
  children,
  continueLabel = 'Open',
  onCancel,
  onContinue,
}: {
  title: string
  children: ReactNode
  continueLabel?: string
  onCancel: () => void
  onContinue: () => void
}) {
  return (
    <div
      className="modal-backdrop"
      onKeyDown={(event) => {
        if (event.key === 'Escape') onCancel()
      }}
    >
      <section
        className="card modal stack"
        role="dialog"
        aria-modal="true"
        aria-labelledby="open-in-browser-title"
        aria-describedby="open-in-browser-text"
        style={{ gap: 12 }}
      >
        <h2 id="open-in-browser-title">{title}</h2>
        <div id="open-in-browser-text" style={{ color: 'var(--text-secondary)' }}>
          {children}
        </div>
        <div className="modal-actions">
          <button type="button" className="button ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="button primary" onClick={onContinue} autoFocus>
            {continueLabel}
          </button>
        </div>
      </section>
    </div>
  )
}
