import { InfoTip } from './InfoTip'
import { Notice } from './Notice'

/** Says plainly, before a backtest runs, that a key or plan served less history than the
 * window asks for, and lets the user run it on the shorter history anyway. */
export function ShortHistoryNotice({
  message,
  onAccept,
  busy = false,
}: {
  message: string
  onAccept: () => void
  busy?: boolean
}) {
  return (
    <Notice tone="warning">
      <div className="stack" style={{ gap: 8 }}>
        <div className="heading-with-info">
          <strong>The backtest has not run: less history than you asked for</strong>
          <InfoTip topic="shortHistory" />
        </div>
        <p style={{ margin: 0 }}>{message}</p>
        <div>
          <button type="button" className="button" disabled={busy} onClick={onAccept}>
            Run on the shorter history
          </button>
        </div>
      </div>
    </Notice>
  )
}
