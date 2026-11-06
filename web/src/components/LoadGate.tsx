import type { ReactNode } from 'react'
import type { PlannedFetch, ProviderEstimate } from '../api/types'
import { useLoadGate, type LoadGateState } from '../lib/loadGate'
import { ASKING_PAGES, limitLabel, resetTime, type AskingPage } from '../lib/usage'
import { InfoTip } from './InfoTip'
import { ErrorNotice } from './Notice'

/**
 * Asks before showing `children`, whose loading calls the user's keys' providers right
 * away, saying about how many requests each key would spend and how much its tightest
 * limit has left (see :func:`useLoadGate`).
 */
export function LoadGate({
  page,
  signature,
  fetches,
  ready = true,
  what,
  title,
  children,
}: {
  page: AskingPage
  /** What the page would show, e.g. its symbols; agreeing covers this alone. */
  signature: string
  fetches: PlannedFetch[]
  /** False while what the page would fetch is still being worked out. */
  ready?: boolean
  /** What loading brings, for the question: "this portfolio's prices". */
  what: string
  /** The page's heading, shown above the question. */
  title?: string
  children: ReactNode
}) {
  const gate = useLoadGate(page, signature, fetches, ready)
  if (gate.allowed) return <>{children}</>
  if (title === undefined) return <LoadGatePanel gate={gate} what={what} />
  return (
    <div className="stack">
      <header className="page-header">
        <h1>{title}</h1>
      </header>
      <LoadGatePanel gate={gate} what={what} />
    </div>
  )
}

/** The question a page asks before loading: what it would spend on each key. */
export function LoadGatePanel({ gate, what }: { gate: LoadGateState; what: string }) {
  if (gate.checking) return <div className="card muted">Checking what this page needs…</div>
  const { estimate, load, page } = gate
  const providers = estimate.data?.providers ?? []
  return (
    <section className="card stack load-gate" style={{ gap: 12 }} aria-live="polite">
      <div className="heading-with-info">
        <h2>Load {what}?</h2>
        <InfoTip topic="loadEstimate" />
      </div>
      {estimate.error !== null && (
        <>
          <ErrorNotice error={estimate.error} />
          <p>PyQxel could not work out what loading would cost. You can load it anyway.</p>
        </>
      )}
      {providers.length > 0 && (
        <div>
          It would send about:
          <ul>
            {providers.map((provider) => (
              <EstimateLine key={provider.provider} estimate={provider} />
            ))}
          </ul>
        </div>
      )}
      {(estimate.data?.from_store ?? 0) > 0 && (
        <p className="disclaimer">
          {estimate.data?.from_store} price{' '}
          {estimate.data?.from_store === 1 ? 'history comes' : 'histories come'} from prices PyQxel
          already keeps for you, at no cost.
        </p>
      )}
      <div className="filter-row">
        <button type="button" className="button primary" onClick={() => load(false)}>
          Load
        </button>
        <button type="button" className="button ghost" onClick={() => load(true)}>
          Load, and don’t ask again for {ASKING_PAGES[page]}
        </button>
      </div>
      <p className="disclaimer">
        You can turn asking back on in Settings, under What spends your plans’ limits.
      </p>
    </section>
  )
}

function EstimateLine({ estimate }: { estimate: ProviderEstimate }) {
  const limit = estimate.tightest
  return (
    <li>
      <strong>
        {estimate.requests.toLocaleString('en-US')}{' '}
        {estimate.requests === 1 ? 'request' : 'requests'}
      </strong>{' '}
      on your {estimate.name} key
      {limit && limit.remaining !== null && limit.remaining !== undefined && (
        <>
          , with {limit.remaining.toLocaleString('en-US')} of its {limitLabel(limit)} left
          {limit.source === 'pyqxel' ? ' by PyQxel’s count' : ''}
          {limit.remaining < estimate.requests && (
            <strong> — not enough; the rest would fall to your other keys</strong>
          )}{' '}
          (it resets at {resetTime(limit)})
        </>
      )}
      {estimate.new_symbols ? (
        <>
          . {estimate.new_symbols} of the symbols count as new toward {estimate.name}’s limit of
          different symbols a month
        </>
      ) : null}
      .
    </li>
  )
}
