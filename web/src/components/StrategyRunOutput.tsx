import type { RunError } from '../runners/types'
import { Notice } from './Notice'

const HEADINGS: Record<RunError['kind'], string> = {
  syntax: 'Syntax error',
  runtime: 'Error',
  result: 'These positions can’t be backtested',
  timeout: 'Stopped: the strategy took too long',
  cancelled: 'Stopped',
  load: 'Python could not start',
}

/** What a strategy run left behind in the browser: its error, and anything it printed. */
export function StrategyRunOutput({ error, output }: { error: RunError | null; output: string }) {
  const shown = error && error.kind !== 'cancelled' ? error : null
  if (!shown && !output) return null
  // A traceback adds the failing code and where it was called from; a plain message
  // repeats itself as its own traceback.
  const traceback = shown?.traceback.trim()
  return (
    <div className="stack" style={{ gap: 10 }}>
      {shown && (
        <Notice tone="error">
          <strong>
            {HEADINGS[shown.kind]}
            {shown.line ? ` on line ${shown.line}` : ''}
          </strong>
          {traceback && traceback !== shown.message ? (
            <pre className="code-output">{traceback}</pre>
          ) : (
            <div>{shown.message}</div>
          )}
        </Notice>
      )}
      {output && (
        <details open className="run-output">
          <summary>Printed output</summary>
          <pre className="code-output">{output}</pre>
        </details>
      )}
    </div>
  )
}
