import { useState } from 'react'
import { ApiError } from '../api/client'
import { ErrorNotice, Notice } from './Notice'

export interface ExportedPrompt {
  prompt: string
  filename: string
}

/**
 * Using Claude outside the app instead of on the account's API key: get exactly the
 * prompt the app would send, take it to Claude, and paste Claude's answer back to be
 * checked and saved as if the app had written it.
 */
export function ExternalClaude({
  getPrompt,
  onPaste,
  saveLabel = 'Save answer',
  disabled = false,
}: {
  /** Builds the prompt; throws to show why it cannot. */
  getPrompt: () => Promise<ExportedPrompt>
  /** Checks and keeps a pasted answer; throws to show what is wrong with it. */
  onPaste: (answer: string) => Promise<void>
  saveLabel?: string
  disabled?: boolean
}) {
  const [exported, setExported] = useState<ExportedPrompt | null>(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const [answer, setAnswer] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const prepare = async () => {
    setLoading(true)
    setError(null)
    setCopied(false)
    try {
      setExported(await getPrompt())
    } catch (failure) {
      setError(failure)
    } finally {
      setLoading(false)
    }
  }

  const copy = async () => {
    if (!exported) return
    try {
      await navigator.clipboard.writeText(exported.prompt)
      setCopied(true)
    } catch {
      setError(new Error('Copying was blocked by the browser; download the prompt instead.'))
    }
  }

  const download = () => {
    if (!exported) return
    const url = URL.createObjectURL(new Blob([exported.prompt], { type: 'text/markdown' }))
    const link = document.createElement('a')
    link.href = url
    link.download = exported.filename
    link.click()
    URL.revokeObjectURL(url)
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      await onPaste(answer)
      setAnswer('')
      setSaved(true)
    } catch (failure) {
      setError(failure)
    } finally {
      setSaving(false)
    }
  }

  return (
    <details className="external-claude">
      <summary>Use Claude outside the app, without API costs</summary>
      <div className="stack" style={{ gap: 12, marginTop: 10 }}>
        <p className="disclaimer">
          Get exactly what the app would send Claude, paste it into a new chat on claude.ai (or
          attach the downloaded file), then paste Claude’s whole answer below. Nothing is sent to
          Claude from here, and it does not count against your AI limits.
        </p>
        <div className="filter-row" style={{ alignItems: 'center' }}>
          <button
            type="button"
            className="button small"
            disabled={disabled || loading}
            onClick={() => void prepare()}
          >
            {loading ? 'Gathering the data…' : exported ? 'Get a fresh prompt' : 'Get the prompt'}
          </button>
          {exported && (
            <>
              <button type="button" className="button primary small" onClick={() => void copy()}>
                {copied ? 'Copied' : 'Copy prompt'}
              </button>
              <button type="button" className="button ghost small" onClick={download}>
                Download {exported.filename}
              </button>
              <span className="disclaimer">
                {exported.prompt.length.toLocaleString()} characters
              </span>
            </>
          )}
        </div>
        {exported && (
          <label className="field">
            <span className="visually-hidden">Prompt for Claude</span>
            <textarea className="prompt-preview" rows={6} readOnly value={exported.prompt} />
          </label>
        )}
        <label className="field">
          <span>Claude’s answer</span>
          <textarea
            rows={4}
            placeholder="Paste Claude’s whole answer here"
            value={answer}
            onChange={(event) => {
              setAnswer(event.target.value)
              setSaved(false)
            }}
          />
        </label>
        <div className="filter-row">
          <button
            type="button"
            className="button primary small"
            disabled={!answer.trim() || saving}
            onClick={() => void save()}
          >
            {saving ? 'Checking…' : saveLabel}
          </button>
        </div>
        {saved && <Notice>Saved.</Notice>}
        {error !== null &&
          (error instanceof ApiError || !(error instanceof Error) ? (
            <ErrorNotice error={error} />
          ) : (
            // Problems found here, such as a blocked clipboard, not on the server.
            <Notice tone="error">{error.message}</Notice>
          ))}
      </div>
    </details>
  )
}
