import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError } from '../api/client'
import {
  exportAnalysisPrompt,
  getAnalysis,
  importAnalysis,
  listAnalyses,
  streamAnalysis,
} from '../api/endpoints'
import { readEvents } from '../api/sse'
import type {
  AnalysisContext,
  AnalysisKind,
  AnalysisRequest,
  AnalysisResponse,
  InvestmentThesis,
  RiskSummary,
} from '../api/types'
import { keepExport, keptExport } from '../lib/exportedPrompts'
import { dateTime } from '../lib/format'
import { LEVEL_TONE } from '../lib/tones'
import { ExternalClaude } from './ExternalClaude'
import { ErrorNotice } from './Notice'
import { Segmented } from './Segmented'
import { InfoTip } from './InfoTip'

const KINDS = [
  { value: 'thesis', label: 'Investment thesis' },
  { value: 'risk', label: 'Risk summary' },
] as const

interface Progress {
  thinking: string
  reportCharacters: number
}

/**
 * The stock's latest stored AI report, and writing a new one. Reports are billed to the
 * account's own Anthropic key, so one is only written when asked for.
 */
export function AnalysisPanel({ symbol }: { symbol: string }) {
  const queryClient = useQueryClient()
  const [kind, setKind] = useState<AnalysisKind>('thesis')
  const [progress, setProgress] = useState<Progress | null>(null)
  const [streamError, setStreamError] = useState<unknown>(null)
  // Reports written in this visit, by kind; the panel is remounted for another symbol.
  const [writtenByKind, setWritten] = useState<Partial<Record<AnalysisKind, AnalysisResponse>>>({})
  const written = writtenByKind[kind] ?? null
  const abort = useRef<AbortController | null>(null)

  useEffect(() => () => abort.current?.abort(), [])

  const changeKind = (next: AnalysisKind) => {
    abort.current?.abort()
    setStreamError(null)
    setKind(next)
  }

  const list = useQuery({ queryKey: ['analyses', symbol], queryFn: () => listAnalyses(symbol) })
  const latest = list.data?.items.find((item) => item.kind === kind)
  const stored = useQuery({
    queryKey: ['analysis', latest?.id],
    queryFn: () => getAnalysis(latest!.id),
    enabled: Boolean(latest) && !written,
    staleTime: Infinity,
  })
  const report = written ?? stored.data ?? null

  const write = async (refresh: boolean) => {
    const controller = new AbortController()
    abort.current = controller
    setStreamError(null)
    setProgress({ thinking: '', reportCharacters: 0 })
    try {
      const response = await streamAnalysis(
        symbol,
        { kind, period: '1y', include_filings: true, refresh },
        controller.signal,
      )
      for await (const event of readEvents(response)) {
        if (event.event === 'thinking' || event.event === 'report') {
          const { text } = JSON.parse(event.data) as { text: string }
          setProgress(
            (current) =>
              current &&
              (event.event === 'thinking'
                ? { ...current, thinking: current.thinking + text }
                : { ...current, reportCharacters: current.reportCharacters + text.length }),
          )
        } else if (event.event === 'result') {
          const result = JSON.parse(event.data) as AnalysisResponse
          setWritten((current) => ({ ...current, [result.kind]: result }))
          void queryClient.invalidateQueries({ queryKey: ['analyses', symbol] })
        } else if (event.event === 'error') {
          const failure = JSON.parse(event.data) as {
            status: number
            detail: string
            retry_after: number | null
          }
          setStreamError(new ApiError(failure.status, failure.detail, failure.retry_after))
        }
      }
    } catch (error) {
      if (!controller.signal.aborted) setStreamError(error)
    } finally {
      setProgress(null)
      abort.current = null
    }
  }

  // Outside the app: the prompt's data is kept so the pasted answer is saved with it.
  const exportName = `stock.${symbol}.${kind}`
  const getPrompt = async () => {
    const request: AnalysisRequest = { kind, period: '1y', include_filings: true }
    const exported = await exportAnalysisPrompt(symbol, request)
    keepExport(exportName, { request, context: exported.context })
    return exported
  }
  const pasteAnswer = async (response: string) => {
    const kept = keptExport<{ request: AnalysisRequest; context: AnalysisContext }>(exportName)
    if (!kept) throw new Error('Get the prompt first: the answer is saved with its data.')
    const result = await importAnalysis(symbol, { ...kept, response })
    setWritten((current) => ({ ...current, [result.kind]: result }))
    void queryClient.invalidateQueries({ queryKey: ['analyses', symbol] })
  }

  const writing = progress !== null
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <div className="heading-with-info">
            <h2>AI research report</h2>
            <InfoTip topic="aiReport" />
          </div>
          <div className="subtitle">
            Written by Claude from prices, factor exposures, a GARCH volatility forecast, peer
            comparisons, financial statements, SEC filings, news, analyst ratings, earnings, insider
            trading, lobbying, government contracts, patents and the market calendar. Billed to your
            Anthropic key.
          </div>
        </div>
        <div className="filter-row">
          <Segmented label="Report kind" value={kind} options={KINDS} onChange={changeKind} />
          {writing ? (
            <button type="button" className="button" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          ) : (
            <button type="button" className="button primary" onClick={() => write(!!report)}>
              {report ? 'Write a new report' : 'Write report'}
            </button>
          )}
        </div>
      </div>

      {streamError !== null && <ErrorNotice error={streamError} />}
      {list.error !== null && <ErrorNotice error={list.error} />}

      {writing ? (
        <div className="stack" style={{ gap: 10 }} aria-live="polite">
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <div className="spinner" />
            <span>
              {progress.reportCharacters > 0
                ? `Writing the report (${progress.reportCharacters.toLocaleString()} characters)…`
                : 'Gathering data and reasoning…'}
            </span>
          </div>
          {progress.thinking && <div className="thinking">{progress.thinking}</div>}
        </div>
      ) : report ? (
        <Report response={report} />
      ) : stored.isLoading || list.isLoading ? (
        <div className="empty">
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <div className="empty">
          No {kind === 'thesis' ? 'thesis' : 'risk summary'} for {symbol} yet.
        </div>
      )}
      <ExternalClaude getPrompt={getPrompt} onPaste={pasteAnswer} disabled={writing} />
    </section>
  )
}

function Report({ response }: { response: AnalysisResponse }) {
  return (
    <article className="report">
      {response.kind === 'thesis' ? (
        <Thesis report={response.report as InvestmentThesis} />
      ) : (
        <Risk report={response.report as RiskSummary} />
      )}
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {response.model} · {dateTime(response.generated_at)}
        {response.cached && ' · reused, not billed again'} · {response.disclaimer}
      </p>
    </article>
  )
}

const STANCE_TONE = { bullish: 'good', neutral: '', bearish: 'critical' } as const

function Thesis({ report }: { report: InvestmentThesis }) {
  return (
    <>
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${STANCE_TONE[report.stance]}`}>Stance: {report.stance}</span>
        <span className="badge">Conviction: {report.conviction}</span>
      </div>
      <p className="headline">{report.headline}</p>
      <p style={{ marginTop: 8 }}>{report.summary}</p>
      <div className="grid-2">
        <Points title="Supporting points" items={report.supporting_points} />
        <Points title="Counterpoints" items={report.counterpoints} />
      </div>
      <Points title="What would change the view" items={report.what_would_change_the_view} />
      <Points title="Data limitations" items={report.data_limitations} />
    </>
  )
}

function Risk({ report }: { report: RiskSummary }) {
  return (
    <>
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${LEVEL_TONE[report.risk_level]}`}>
          Risk level: {report.risk_level}
        </span>
      </div>
      <p className="headline">{report.headline}</p>
      <p style={{ marginTop: 8 }}>{report.summary}</p>
      <h3>Volatility</h3>
      <p>{report.volatility_assessment}</p>
      <h3>Drawdowns</h3>
      <p>{report.drawdown_assessment}</p>
      <h3>Key risks</h3>
      <ul>
        {report.key_risks.map((risk) => (
          <li key={risk.name}>
            <strong>{risk.name}</strong>{' '}
            <span className={`badge ${LEVEL_TONE[risk.severity]}`}>{risk.severity}</span>
            <div style={{ color: 'var(--text-secondary)' }}>{risk.evidence}</div>
          </li>
        ))}
      </ul>
      <Points title="Data limitations" items={report.data_limitations} />
    </>
  )
}

export function Points({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}
