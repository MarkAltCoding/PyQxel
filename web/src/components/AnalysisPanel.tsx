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
  PriceSummary,
  RiskSummary,
} from '../api/types'
import { keepExport, keptExport } from '../lib/exportedPrompts'
import { dateTime, percent, signedPercent, tone } from '../lib/format'
import { useReportView } from '../lib/reportView'
import { badgeTone, SEVERITY_ORDER, SEVERITY_TONE } from '../lib/tones'
import { ExternalClaude } from './ExternalClaude'
import { ErrorNotice } from './Notice'
import {
  FinePrint,
  FullText,
  LevelMeter,
  PointColumn,
  ReportTabs,
  ReportViewToggle,
  RiskCards,
  StanceMeter,
  Summary,
  TextPoints,
  TextRisks,
  Verdict,
  type Tab,
} from './ReportParts'
import { Segmented } from './Segmented'
import { Stat } from './Stat'
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
  const [view, setView] = useReportView()
  const prices = response.context.prices
  const thesis = response.kind === 'thesis' ? (response.report as InvestmentThesis) : null
  const risk = response.kind === 'thesis' ? null : (response.report as RiskSummary)
  return (
    <article className="report">
      <ReportViewToggle view={view} onChange={setView} />
      {thesis &&
        (view === 'text' ? (
          <TextThesis report={thesis} />
        ) : (
          <Thesis report={thesis} prices={prices} />
        ))}
      {risk &&
        (view === 'text' ? <TextRisk report={risk} /> : <Risk report={risk} prices={prices} />)}
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {response.model} · {dateTime(response.generated_at)}
        {response.cached && ' · reused, not billed again'} · {response.disclaimer}
      </p>
    </article>
  )
}

/** The price figures the report was written from, so its claims can be checked at a
 * glance. */
function PriceFacts({ prices }: { prices: PriceSummary }) {
  return (
    <div className="stats">
      <Stat
        info="annualizedReturn"
        label="Return, annualized"
        value={signedPercent(prices.annualized_return)}
        className={tone(prices.annualized_return)}
      />
      <Stat
        info="realizedVolatility"
        label="Volatility"
        value={percent(prices.realized_volatility)}
      />
      <Stat info="maxDrawdown" label="Max drawdown" value={percent(prices.max_drawdown)} />
      <Stat
        info="currentDrawdown"
        label="Below its high"
        value={percent(prices.current_drawdown)}
      />
    </div>
  )
}

function Thesis({ report, prices }: { report: InvestmentThesis; prices: PriceSummary }) {
  const tabs: Tab[] = [
    {
      id: 'overview',
      label: 'Overview',
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <PriceFacts prices={prices} />
          <Summary text={report.summary} />
        </div>
      ),
    },
    {
      id: 'case',
      label: 'Bull vs. bear',
      count: report.supporting_points.length + report.counterpoints.length,
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <EvidenceBalance
            support={report.supporting_points.length}
            against={report.counterpoints.length}
          />
          <div className="grid-2">
            <PointColumn title="Supporting" items={report.supporting_points} tone="good" />
            <PointColumn title="Against" items={report.counterpoints} tone="critical" />
          </div>
        </div>
      ),
    },
    {
      id: 'watch',
      label: 'What would change it',
      count: report.what_would_change_the_view.length,
      content: (
        <PointColumn title="Watch for" items={report.what_would_change_the_view} tone="warning" />
      ),
    },
    {
      id: 'limits',
      label: 'Limits',
      count: report.data_limitations.length,
      content: <FinePrint title="What the data cannot show" items={report.data_limitations} />,
    },
  ]
  return (
    <>
      <Verdict headline={report.headline}>
        <StanceMeter value={report.stance} info="stance" />
        <LevelMeter
          label="Conviction"
          info="conviction"
          steps={['low', 'medium', 'high']}
          value={report.conviction}
          tone="neutral"
        />
      </Verdict>
      <ReportTabs label="Parts of the thesis" tabs={tabs} />
    </>
  )
}

/** How many points Claude found for the stance and against it, as one split bar. */
function EvidenceBalance({ support, against }: { support: number; against: number }) {
  const total = support + against
  if (total === 0) return null
  return (
    <div className="evidence-balance">
      <div
        className="evidence-bar"
        role="img"
        aria-label={`${support} points for, ${against} against`}
      >
        <span className="tone-good" style={{ flexGrow: support }} />
        <span className="tone-critical" style={{ flexGrow: against }} />
      </div>
      <div className="evidence-legend">
        <span className="up">{support} for</span>
        <span className="down">{against} against</span>
      </div>
    </div>
  )
}

function Risk({ report, prices }: { report: RiskSummary; prices: PriceSummary }) {
  const tabs: Tab[] = [
    {
      id: 'overview',
      label: 'Overview',
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <PriceFacts prices={prices} />
          <Summary text={report.summary} />
        </div>
      ),
    },
    {
      id: 'risks',
      label: 'Key risks',
      count: report.key_risks.length,
      content: <RiskCards risks={report.key_risks} />,
    },
    {
      id: 'swings',
      label: 'Swings and falls',
      content: (
        <div className="grid-2">
          <div className="assessment">
            <h3>Volatility</h3>
            <p>{report.volatility_assessment}</p>
          </div>
          <div className="assessment">
            <h3>Drawdowns</h3>
            <p>{report.drawdown_assessment}</p>
          </div>
        </div>
      ),
    },
    {
      id: 'limits',
      label: 'Limits',
      count: report.data_limitations.length,
      content: <FinePrint title="What the data cannot show" items={report.data_limitations} />,
    },
  ]
  return (
    <>
      <Verdict headline={report.headline}>
        <LevelMeter
          label="Risk level"
          info="riskLevel"
          steps={SEVERITY_ORDER}
          value={report.risk_level}
          tone={SEVERITY_TONE[report.risk_level]}
        />
      </Verdict>
      <ReportTabs label="Parts of the risk summary" tabs={tabs} />
    </>
  )
}

// The full-text view: the whole report on one page.

const STANCE_TONE = { bullish: 'good', neutral: '', bearish: 'critical' } as const

function TextThesis({ report }: { report: InvestmentThesis }) {
  return (
    <>
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${STANCE_TONE[report.stance]}`}>
          Stance: {report.stance}
          <InfoTip topic="stance" />
        </span>
        <span className="badge">
          Conviction: {report.conviction}
          <InfoTip topic="conviction" />
        </span>
      </div>
      <p className="headline">{report.headline}</p>
      <FullText text={report.summary} />
      <div className="grid-2">
        <TextPoints title="Supporting points" items={report.supporting_points} />
        <TextPoints title="Counterpoints" items={report.counterpoints} />
      </div>
      <TextPoints title="What would change the view" items={report.what_would_change_the_view} />
      <TextPoints title="Data limitations" items={report.data_limitations} />
    </>
  )
}

function TextRisk({ report }: { report: RiskSummary }) {
  return (
    <>
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${badgeTone(SEVERITY_TONE[report.risk_level])}`}>
          Risk level: {report.risk_level}
          <InfoTip topic="riskLevel" />
        </span>
      </div>
      <p className="headline">{report.headline}</p>
      <FullText text={report.summary} />
      <h3>Volatility</h3>
      <p>{report.volatility_assessment}</p>
      <h3>Drawdowns</h3>
      <p>{report.drawdown_assessment}</p>
      <h3>Key risks</h3>
      <TextRisks risks={report.key_risks} />
      <TextPoints title="Data limitations" items={report.data_limitations} />
    </>
  )
}
