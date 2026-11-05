import { useState } from 'react'
import { readStored, writeStored } from '../lib/storage'

const STORAGE_KEY = 'pyqxel.adviceNoticeAccepted'

/**
 * Says once, before anything else, that PyQxel is a research tool and not investment
 * advice, and asks the person to acknowledge it. App stores expect finance apps to say so
 * up front, not only in a footer. The answer is kept on this device.
 */
export function AdviceNotice() {
  const [accepted, setAccepted] = useState(() => readStored(STORAGE_KEY) !== null)
  if (accepted) return null
  const accept = () => {
    writeStored(STORAGE_KEY, new Date().toISOString())
    setAccepted(true)
  }
  return (
    <div className="modal-backdrop">
      <section
        className="card modal stack"
        role="dialog"
        aria-modal="true"
        aria-labelledby="advice-notice-title"
        style={{ gap: 12 }}
      >
        <h2 id="advice-notice-title">Not investment advice</h2>
        <p>
          PyQxel is a research tool. Its prices, statistics, backtests, simulations, screens and
          AI-written reports help you study securities, but they are not recommendations to buy,
          sell or hold anything, and PyQxel is not a financial adviser or broker.
        </p>
        <p>
          Backtests and simulations replay or model the past, and past results do not predict future
          ones. Data can be late or wrong, and AI reports can make mistakes. Check what matters with
          other sources, and consider a licensed adviser before you invest.
        </p>
        <button type="button" className="button primary" onClick={accept} autoFocus>
          I understand
        </button>
      </section>
    </div>
  )
}
