import type { ReactNode } from 'react'
import type { WaitPlans } from '../api/types'
import { WAIT_CHOICES } from '../lib/waitForData'
import { AdditionalContextField } from './AdditionalContext'
import { WaitForDataOption } from './WaitForData'
import { WebSearchOption } from './WebSearch'

/**
 * The choices every AI report shares (web search, how long to wait for data, and context
 * to weigh), folded into one row whose summary always says what is chosen, so the card
 * stays short and nothing is hidden.
 */
export function AiOptions({
  webSearch,
  onWebSearch,
  wait,
  onWait,
  plans,
  context,
  onContext,
  disabled = false,
  children,
}: {
  webSearch: boolean
  onWebSearch: (checked: boolean) => void
  wait: number
  onWait: (seconds: number) => void
  /** What each wait would gather for this request, from the server. */
  plans?: WaitPlans
  context: string
  onContext: (text: string) => void
  disabled?: boolean
  /** Choices of this report's own, shown beside the shared ones. */
  children?: ReactNode
}) {
  const waitLabel = WAIT_CHOICES.find((choice) => choice.value === wait)?.label
  const state = [
    webSearch ? 'Web search on' : 'Web search off',
    wait === 0 ? 'No waiting for data' : `Waits ${waitLabel?.toLowerCase() ?? ''} for data`,
    context.trim()
      ? `${context.length.toLocaleString('en-US')} characters of context`
      : 'No added context',
  ]
  return (
    <details className="ai-options">
      <summary>
        <span className="ai-options-title">Options</span>
        <span className="ai-options-state">{state.join(' · ')}</span>
      </summary>
      <div className="ai-options-body">
        <div className="filter-row" style={{ alignItems: 'center' }}>
          <WebSearchOption checked={webSearch} onChange={onWebSearch} disabled={disabled} />
          <WaitForDataOption seconds={wait} onChange={onWait} disabled={disabled} plans={plans} />
        </div>
        {children}
        <AdditionalContextField value={context} onChange={onContext} disabled={disabled} />
      </div>
    </details>
  )
}
