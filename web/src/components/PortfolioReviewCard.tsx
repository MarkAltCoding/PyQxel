import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { readAiStream, type AiProgress } from '../api/aiStream'
import {
  exportPortfolioPrompt,
  getPortfolioReview,
  importPortfolioReview,
  streamPortfolioReview,
} from '../api/endpoints'
import type {
  InvestorProfile,
  PortfolioContext,
  PortfolioAnalysisRequest,
  PortfolioAnalysisResponse,
  RebalancePlan,
} from '../api/types'
import { dateTime, money, number, percent } from '../lib/format'
import { keepExport, keptExport } from '../lib/exportedPrompts'
import { LEVEL_TONE } from '../lib/tones'
import { ExternalClaude } from './ExternalClaude'
import { Points } from './AnalysisPanel'
import { ErrorNotice } from './Notice'
import { Segmented } from './Segmented'

type Period = NonNullable<PortfolioAnalysisRequest['period']>

const PERIODS: { value: Period; label: string }[] = [
  { value: '1y', label: '1 year' },
  { value: '2y', label: '2 years' },
  { value: '5y', label: '5 years' },
]

type Risk = NonNullable<InvestorProfile['risk_tolerance']> | 'unset'

const RISKS: { value: Risk; label: string }[] = [
  { value: 'unset', label: 'Not set' },
  { value: 'conservative', label: 'Conservative' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'aggressive', label: 'Aggressive' },
]

const DIVERSIFICATION_TONE = {
  poor: 'critical',
  fair: 'warning',
  good: '',
  strong: 'good',
} as const

/**
 * Claude's review of the saved portfolio: the latest one stored, and writing a new one,
 * billed to the account's own Anthropic key, so one is only written when asked for.
 */
export function PortfolioReviewCard({
  hasHoldings,
  stale,
}: {
  hasHoldings: boolean
  stale: boolean
}) {
  const queryClient = useQueryClient()
  const [period, setPeriod] = useState<Period>('1y')
  const [risk, setRisk] = useState<Risk>('unset')
  const [horizon, setHorizon] = useState('')
  const [cash, setCash] = useState('')
  const [notes, setNotes] = useState('')
  const [progress, setProgress] = useState<AiProgress | null>(null)
  const [error, setError] = useState<unknown>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const latest = useQuery({ queryKey: ['portfolio-review'], queryFn: getPortfolioReview })
  const review = latest.data ?? null

  const profile = (): InvestorProfile => ({
    risk_tolerance: risk === 'unset' ? null : risk,
    horizon_years: horizon.trim() ? Number(horizon) : null,
    cash_to_invest: cash.trim() ? Number(cash) : 0,
    notes: notes.trim() || null,
  })

  // Outside the app: the prompt's data is kept so the pasted answer is saved with it.
  const getPrompt = async () => {
    const request: PortfolioAnalysisRequest = { period, profile: profile() }
    const exported = await exportPortfolioPrompt(request)
    keepExport('portfolio', { request, context: exported.context })
    return exported
  }
  const pasteAnswer = async (response: string) => {
    const kept = keptExport<{ request: PortfolioAnalysisRequest; context: PortfolioContext }>(
      'portfolio',
    )
    if (!kept) throw new Error('Get the prompt first: the answer is saved with its data.')
    queryClient.setQueryData(
      ['portfolio-review'],
      await importPortfolioReview({ ...kept, response }),
    )
  }

  const write = async () => {
    const controller = new AbortController()
    abort.current = controller
    setError(null)
    setProgress({ thinking: '', characters: 0 })
    try {
      const response = await streamPortfolioReview(
        { period, profile: profile(), refresh: review?.period === period },
        controller.signal,
      )
      const result = await readAiStream<PortfolioAnalysisResponse>(response, setProgress)
      queryClient.setQueryData(['portfolio-review'], result)
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure)
    } finally {
      setProgress(null)
      abort.current = null
    }
  }

  const writing = progress !== null
  const blocked = !hasHoldings || stale
  return (
    <section className="card">
      <div className="card-header">
        <div>
          <h2>AI portfolio review</h2>
          <div className="subtitle">
            Written by Claude from your saved holdings: where the risk comes from, how well the
            holdings diversify one another, and a specific plan with the orders to carry it out.
            Billed to your Anthropic key.
          </div>
        </div>
        <div className="filter-row">
          <Segmented label="History" value={period} options={PERIODS} onChange={setPeriod} />
          {writing ? (
            <button type="button" className="button" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          ) : (
            <button
              type="button"
              className="button primary"
              disabled={blocked}
              onClick={() => void write()}
            >
              {review ? 'Write a new review' : 'Review my portfolio'}
            </button>
          )}
        </div>
      </div>

      <details style={{ marginBottom: 12 }}>
        <summary>Tailor the plan to your goals (optional)</summary>
        <div className="filter-row" style={{ marginTop: 10, alignItems: 'flex-end' }}>
          <Segmented label="Risk tolerance" value={risk} options={RISKS} onChange={setRisk} />
          <label className="field" style={{ width: 140 }}>
            <span>Horizon (years)</span>
            <input
              type="number"
              min={0}
              max={60}
              value={horizon}
              onChange={(e) => setHorizon(e.target.value)}
            />
          </label>
          <label className="field" style={{ width: 160 }}>
            <span>New cash to invest</span>
            <input type="number" min={0} value={cash} onChange={(e) => setCash(e.target.value)} />
          </label>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span>Anything else</span>
            <input
              type="text"
              maxLength={1000}
              placeholder="e.g. taxable account; keep AAPL; no tobacco"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
        </div>
      </details>

      {error !== null && <ErrorNotice error={error} />}
      {latest.error !== null && <ErrorNotice error={latest.error} />}

      {writing ? (
        <div className="stack" style={{ gap: 10 }} aria-live="polite">
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <div className="spinner" />
            <span>
              {progress.characters > 0
                ? `Writing the review (${progress.characters.toLocaleString()} characters)…`
                : 'Measuring the portfolio and reasoning…'}
            </span>
          </div>
          {progress.thinking && <div className="thinking">{progress.thinking}</div>}
        </div>
      ) : review ? (
        <Review response={review} />
      ) : latest.isLoading ? (
        <div className="empty">
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        <div className="empty">
          {!hasHoldings
            ? 'Save holdings with prices to have them reviewed.'
            : stale
              ? 'Save your changes to review the new holdings.'
              : 'No review yet.'}
        </div>
      )}
      <ExternalClaude getPrompt={getPrompt} onPaste={pasteAnswer} disabled={writing || blocked} />
    </section>
  )
}

function Review({ response }: { response: PortfolioAnalysisResponse }) {
  const { report, context } = response
  return (
    <article className="report">
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${LEVEL_TONE[report.risk_level]}`}>
          Risk level: {report.risk_level}
        </span>
        <span className={`badge ${DIVERSIFICATION_TONE[report.diversification]}`}>
          Diversification: {report.diversification}
        </span>
        <span className="badge">
          Volatility {percent(context.portfolio.volatility)} · max drawdown{' '}
          {percent(context.portfolio.max_drawdown)}
        </span>
      </div>
      <p className="headline">{report.headline}</p>
      <p style={{ marginTop: 8 }}>{report.summary}</p>
      <div className="grid-2">
        <Points title="Strengths" items={report.strengths} />
        <div>
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
        </div>
      </div>
      <h3>Recommended plan</h3>
      <p>{report.plan_summary}</p>
      <TargetTable response={response} />
      {response.plan && <Orders plan={response.plan} />}
      <Points title="Trade-offs and what would change the plan" items={report.considerations} />
      <Points title="Data limitations" items={report.data_limitations} />
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {response.model} · {dateTime(response.generated_at)} · {response.period} of history
        {response.cached && ' · reused, not billed again'} · {response.disclaimer}
      </p>
    </article>
  )
}

function TargetTable({ response }: { response: PortfolioAnalysisResponse }) {
  const current = new Map(response.context.holdings.map((h) => [h.symbol, h.weight]))
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th>Position</th>
            <th className="num">Now</th>
            <th className="num">Target</th>
            <th>Why</th>
          </tr>
        </thead>
        <tbody>
          {response.report.target_allocation.map((target) => (
            <tr key={target.symbol}>
              <td>{target.symbol.toUpperCase()}</td>
              <td className="num">{percent(current.get(target.symbol.toUpperCase()) ?? 0)}</td>
              <td className="num">{percent(target.target_weight)}</td>
              <td>{target.rationale}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Orders({ plan }: { plan: RebalancePlan }) {
  return (
    <>
      <h3>Orders</h3>
      {plan.trades.length === 0 ? (
        <p>No trades: the portfolio is already at its targets.</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Order</th>
                <th>Symbol</th>
                <th className="num">Shares</th>
                <th className="num">Price</th>
                <th className="num">About</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {plan.trades.map((trade) => (
                <tr key={`${trade.action}-${trade.symbol}`}>
                  <td>
                    <span className={`badge ${trade.action === 'buy' ? 'good' : 'warning'}`}>
                      {trade.action === 'buy' ? 'Buy' : 'Sell'}
                    </span>
                  </td>
                  <td>{trade.symbol}</td>
                  <td className="num">{trade.shares == null ? '—' : number(trade.shares, 0)}</td>
                  <td className="num">{money(trade.price)}</td>
                  <td className="num">{money(trade.value)}</td>
                  <td>
                    {trade.order_type === 'limit'
                      ? `Limit at ${money(trade.limit_price)}`
                      : 'Market'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="disclaimer">
        Sized at the latest prices from {money(plan.total_value)}, including any new cash;{' '}
        {money(plan.cash_after)} would be left as cash. Prices move: check them, and place sells
        before buys.
      </p>
      {(plan.notes ?? []).map((note) => (
        <p key={note} className="disclaimer">
          {note}
        </p>
      ))}
    </>
  )
}
