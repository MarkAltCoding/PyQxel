import { useEffect, useRef, useState, type FormEvent } from 'react'
import { readAiStream, type AiProgress } from '../api/aiStream'
import { streamAssistant } from '../api/endpoints'
import type { AssistantResponse, AssistantTurn, CodeLanguage, LastRun } from '../api/types'
import { ErrorNotice } from './Notice'

/** Earlier turns sent with each question; the server takes at most 20. */
const MAX_TURNS = 20
/** Longest earlier turn the server takes: a full revision of the code and its explanation. */
const MAX_TURN_LENGTH = 58_000
const MAX_MESSAGE_LENGTH = 4_000

const LANGUAGE_NAMES: Record<CodeLanguage, string> = { python: 'python', cpp: 'cpp' }

type Turn =
  | { role: 'user'; text: string }
  | { role: 'assistant'; explanation: string; code: string | null; applied: boolean }

/** A turn as the API takes it: Claude's revisions are kept, so it knows what it proposed. */
function toApiTurn(turn: Turn, language: CodeLanguage): AssistantTurn {
  if (turn.role === 'user') return { role: 'user', content: turn.text }
  const proposal = turn.code
    ? `\n\nThe revised code I proposed${turn.applied ? ', which they applied' : ''}:\n` +
      `\`\`\`${LANGUAGE_NAMES[language]}\n${turn.code}\n\`\`\``
    : ''
  return { role: 'assistant', content: (turn.explanation + proposal).slice(0, MAX_TURN_LENGTH) }
}

/**
 * Claude as a coding assistant for the custom strategy in the editor. Each question goes
 * with the editor's code and how its last run went; the conversation stays in this page
 * and is never stored. Answers are billed to the account's own Anthropic key.
 */
export function StrategyAssistant({
  language,
  code,
  symbol,
  interval,
  lastRun,
  onApply,
}: {
  language: CodeLanguage
  code: string
  symbol: string
  interval: string
  /** How the code now in the editor fared when last run; null if it has not been run. */
  lastRun: LastRun | null
  onApply: (code: string) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [message, setMessage] = useState('')
  const [progress, setProgress] = useState<AiProgress | null>(null)
  const [error, setError] = useState<unknown>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const ask = async (question: string) => {
    const text = question.trim().slice(0, MAX_MESSAGE_LENGTH)
    if (!text) return
    const controller = new AbortController()
    abort.current = controller
    const history = turns.slice(-MAX_TURNS).map((turn) => toApiTurn(turn, language))
    setTurns((current) => [...current, { role: 'user', text }])
    setMessage('')
    setError(null)
    setProgress({ thinking: '', characters: 0 })
    try {
      const response = await streamAssistant(
        {
          language,
          code,
          message: text,
          history,
          symbol: symbol || null,
          interval,
          last_run: lastRun,
        },
        controller.signal,
      )
      const { reply } = await readAiStream<AssistantResponse>(response, setProgress)
      setTurns((current) => [
        ...current,
        {
          role: 'assistant',
          explanation: reply.explanation,
          code: reply.code ?? null,
          applied: false,
        },
      ])
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure)
    } finally {
      setProgress(null)
      abort.current = null
    }
  }

  const apply = (index: number) => {
    const turn = turns[index]
    if (turn?.role !== 'assistant' || !turn.code) return
    onApply(turn.code)
    setTurns((current) =>
      current.map((t, i) => (i === index && t.role === 'assistant' ? { ...t, applied: true } : t)),
    )
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    void ask(message)
  }

  const asking = progress !== null
  const failed = lastRun?.error_kind != null
  const suggestions = [
    ...(failed ? ['Fix the error from my last run.'] : []),
    'Explain what this strategy does.',
    'Check this code for look-ahead bias.',
  ]
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="filter-row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h3>Ask Claude</h3>
          <div className="disclaimer">
            Claude sees your code and how its last run went, and can rewrite it for you. Billed to
            your Anthropic key; the conversation is not saved.
          </div>
        </div>
        {turns.length > 0 && !asking && (
          <button type="button" className="button ghost small" onClick={() => setTurns([])}>
            Clear conversation
          </button>
        )}
      </div>

      {turns.map((turn, index) =>
        turn.role === 'user' ? (
          <div key={index} className="assistant-turn user">
            {turn.text}
          </div>
        ) : (
          <div key={index} className="assistant-turn">
            <div style={{ whiteSpace: 'pre-wrap' }}>{turn.explanation}</div>
            {turn.code && (
              <div className="stack" style={{ gap: 6, marginTop: 8 }}>
                <div className="filter-row" style={{ alignItems: 'center' }}>
                  <button
                    type="button"
                    className="button primary small"
                    disabled={turn.applied || turn.code === code}
                    onClick={() => apply(index)}
                  >
                    {turn.applied || turn.code === code ? 'Applied' : 'Apply to editor'}
                  </button>
                  <span className="disclaimer">Replaces the code in the editor.</span>
                </div>
                <details>
                  <summary>Show the revised code</summary>
                  <pre className="assistant-code">{turn.code}</pre>
                </details>
              </div>
            )}
          </div>
        ),
      )}

      {asking && (
        <div className="stack" style={{ gap: 8 }} aria-live="polite">
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <div className="spinner" />
            <span>{progress.characters > 0 ? 'Writing an answer…' : 'Reading your code…'}</span>
            <button
              type="button"
              className="button ghost small"
              onClick={() => abort.current?.abort()}
            >
              Stop
            </button>
          </div>
          {progress.thinking && <div className="thinking">{progress.thinking}</div>}
        </div>
      )}
      {error !== null && <ErrorNotice error={error} />}

      {!asking && (
        <div className="filter-row">
          {suggestions.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="button ghost small"
              onClick={() => void ask(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      )}
      <form onSubmit={submit} className="filter-row" style={{ alignItems: 'flex-end' }}>
        <label className="field" style={{ flex: 1 }}>
          <span className="visually-hidden">Question for Claude</span>
          <textarea
            rows={2}
            maxLength={MAX_MESSAGE_LENGTH}
            placeholder="Ask about your strategy, or describe a change, e.g. “add a 10% trailing stop”"
            value={message}
            disabled={asking}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                void ask(message)
              }
            }}
          />
        </label>
        <button type="submit" className="button primary" disabled={asking || !message.trim()}>
          Ask
        </button>
      </form>
    </div>
  )
}
