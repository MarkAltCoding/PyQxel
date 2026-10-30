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
  PlannedTrade,
  RebalancePlan,
  TargetPosition,
} from '../api/types'
import { dateTime, money, number, percent } from '../lib/format'
import { keepExport, keptExport } from '../lib/exportedPrompts'
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
  Summary,
  TextPoints,
  TextRisks,
  Verdict,
  type Tab,
} from './ReportParts'
import { Segmented } from './Segmented'
import { Stat } from './Stat'
import { InfoTip } from './InfoTip'

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

/**
 * Claude's review of the saved portfolio: the latest one stored, and writing a new one,
 * billed to the account's own Anthropic key, so one is only written when asked for.
 */
export function PortfolioReviewCard({
  portfolio,
  hasHoldings,
  stale,
}: {
  /** Which saved portfolio to review. */
  portfolio: number
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

  const latest = useQuery({
    queryKey: ['portfolio-review', portfolio],
    queryFn: () => getPortfolioReview(portfolio),
  })
  const review = latest.data ?? null

  const profile = (): InvestorProfile => ({
    risk_tolerance: risk === 'unset' ? null : risk,
    horizon_years: horizon.trim() ? Number(horizon) : null,
    cash_to_invest: cash.trim() ? Number(cash) : 0,
    notes: notes.trim() || null,
  })

  // Outside the app: the prompt's data is kept so the pasted answer is saved with it.
  const getPrompt = async () => {
    const request: PortfolioAnalysisRequest = { portfolio, period, profile: profile() }
    const exported = await exportPortfolioPrompt(request)
    keepExport(`portfolio-${portfolio}`, { request, context: exported.context })
    return exported
  }
  const pasteAnswer = async (response: string) => {
    const kept = keptExport<{ request: PortfolioAnalysisRequest; context: PortfolioContext }>(
      `portfolio-${portfolio}`,
    )
    if (!kept) throw new Error('Get the prompt first: the answer is saved with its data.')
    queryClient.setQueryData(
      ['portfolio-review', portfolio],
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
        { portfolio, period, profile: profile(), refresh: review?.period === period },
        controller.signal,
      )
      const result = await readAiStream<PortfolioAnalysisResponse>(response, setProgress)
      queryClient.setQueryData(['portfolio-review', portfolio], result)
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
          <div className="heading-with-info">
            <h2>AI portfolio review</h2>
            <InfoTip topic="portfolioReview" />
          </div>
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

const DIVERSIFICATION_STEPS = ['poor', 'fair', 'good', 'strong'] as const
const DIVERSIFICATION_METER = {
  poor: 'critical',
  fair: 'warning',
  good: 'neutral',
  strong: 'good',
} as const

function Review({ response }: { response: PortfolioAnalysisResponse }) {
  const [view, setView] = useReportView()
  return (
    <article className="report">
      <ReportViewToggle view={view} onChange={setView} />
      {view === 'text' ? <TextReview response={response} /> : <VisualReview response={response} />}
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {response.model} · {dateTime(response.generated_at)} · {response.period} of history
        {response.cached && ' · reused, not billed again'} · {response.disclaimer}
      </p>
    </article>
  )
}

function VisualReview({ response }: { response: PortfolioAnalysisResponse }) {
  const { report, context } = response
  const stats = context.portfolio
  const tabs: Tab[] = [
    {
      id: 'overview',
      label: 'Overview',
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <div className="stats">
            <Stat
              info="annualizedVolatility"
              label="Volatility"
              value={percent(stats.volatility)}
            />
            <Stat info="maxDrawdown" label="Max drawdown" value={percent(stats.max_drawdown)} />
            <Stat
              info="diversificationRatio"
              label="Diversification ratio"
              value={number(stats.diversification_ratio)}
            />
            <Stat
              info="effectiveHoldings"
              label="Effective holdings"
              value={number(stats.effective_holdings, 1)}
              detail={`of ${context.holdings.length} held`}
            />
          </div>
          <Summary text={report.summary} />
        </div>
      ),
    },
    {
      id: 'assessment',
      label: 'Strengths and risks',
      count: report.strengths.length + report.key_risks.length,
      content: (
        <div className="grid-2">
          <PointColumn title="Strengths" items={report.strengths} tone="good" />
          <div>
            <h3>
              Key risks <span className="report-tab-count">{report.key_risks.length}</span>
            </h3>
            <RiskCards risks={report.key_risks} />
          </div>
        </div>
      ),
    },
    {
      id: 'plan',
      label: 'Plan',
      count: response.plan?.trades.length,
      content: (
        <div className="stack" style={{ gap: 16 }}>
          <p className="plan-summary">{report.plan_summary}</p>
          <AllocationChart response={response} />
          {response.plan && <Orders plan={response.plan} />}
        </div>
      ),
    },
    {
      id: 'tradeoffs',
      label: 'Trade-offs',
      count: report.considerations.length,
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <PointColumn title="Weigh before acting" items={report.considerations} tone="warning" />
          <FinePrint title="What the data cannot show" items={report.data_limitations} />
        </div>
      ),
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
        <LevelMeter
          label="Diversification"
          info="diversification"
          steps={DIVERSIFICATION_STEPS}
          value={report.diversification}
          tone={DIVERSIFICATION_METER[report.diversification]}
        />
      </Verdict>
      <ReportTabs label="Parts of the review" tabs={tabs} />
    </>
  )
}

/** The whole review on one page, every rationale written out. */
function TextReview({ response }: { response: PortfolioAnalysisResponse }) {
  const { report, context } = response
  return (
    <>
      <div className="filter-row" style={{ alignItems: 'center', marginBottom: 8 }}>
        <span className={`badge ${badgeTone(SEVERITY_TONE[report.risk_level])}`}>
          Risk level: {report.risk_level}
          <InfoTip topic="riskLevel" />
        </span>
        <span className={`badge ${badgeTone(DIVERSIFICATION_METER[report.diversification])}`}>
          Diversification: {report.diversification}
          <InfoTip topic="diversification" />
        </span>
        <span className="badge">
          Volatility {percent(context.portfolio.volatility)}
          <InfoTip topic="annualizedVolatility" />
        </span>
        <span className="badge">
          Max drawdown {percent(context.portfolio.max_drawdown)}
          <InfoTip topic="maxDrawdown" />
        </span>
      </div>
      <p className="headline">{report.headline}</p>
      <FullText text={report.summary} />
      <div className="grid-2">
        <TextPoints title="Strengths" items={report.strengths} />
        <div>
          <h3>Key risks</h3>
          <TextRisks risks={report.key_risks} />
        </div>
      </div>
      <h3>Recommended plan</h3>
      <p>{report.plan_summary}</p>
      <TargetTable response={response} />
      {response.plan && <Orders plan={response.plan} />}
      <TextPoints title="Trade-offs and what would change the plan" items={report.considerations} />
      <TextPoints title="Data limitations" items={report.data_limitations} />
    </>
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
            <th className="num">
              Target
              <InfoTip topic="holdingWeight" />
            </th>
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

/** Each position's weight now and Claude's target, biggest change first, with the
 * reason a click away. */
function AllocationChart({ response }: { response: PortfolioAnalysisResponse }) {
  const current = new Map(response.context.holdings.map((h) => [h.symbol, h.weight]))
  const rows = response.report.target_allocation
    .map((target: TargetPosition) => {
      const symbol = target.symbol.toUpperCase()
      const now = current.get(symbol) ?? 0
      return { symbol, now, target: target.target_weight, why: target.rationale }
    })
    .sort((a, b) => Math.abs(b.target - b.now) - Math.abs(a.target - a.now))
  const scale = Math.max(0.01, ...rows.flatMap((row) => [row.now, row.target]))
  return (
    <div className="allocation">
      <div className="heading-with-info">
        <h3>Allocation: now and target</h3>
        <InfoTip topic="holdingWeight" />
      </div>
      <div className="allocation-legend" aria-hidden="true">
        <span className="swatch now" /> Now <span className="swatch target" /> Target
      </div>
      <ul>
        {rows.map((row) => {
          const change = row.target - row.now
          const label =
            Math.abs(change) < 0.0005
              ? 'Hold'
              : `${change > 0 ? '▲ +' : '▼ −'}${(Math.abs(change) * 100).toFixed(1)} pts`
          return (
            <li key={row.symbol}>
              <details>
                <summary>
                  <span className="allocation-symbol">{row.symbol}</span>
                  <span
                    className="allocation-bars"
                    role="img"
                    aria-label={`${row.symbol}: ${percent(row.now)} now, ${percent(row.target)} target`}
                  >
                    <span className="bar now" style={{ width: `${(row.now / scale) * 100}%` }} />
                    <span
                      className="bar target"
                      style={{ width: `${(row.target / scale) * 100}%` }}
                    />
                  </span>
                  <span className="allocation-weights">
                    {percent(row.now)} → {percent(row.target)}
                  </span>
                  <span className={`allocation-change${Math.abs(change) < 0.0005 ? ' muted' : ''}`}>
                    {label}
                  </span>
                </summary>
                <p>{row.why}</p>
              </details>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

function Orders({ plan }: { plan: RebalancePlan }) {
  const sells = plan.trades.filter((trade) => trade.action === 'sell')
  const buys = plan.trades.filter((trade) => trade.action === 'buy')
  const total = (trades: PlannedTrade[]) => trades.reduce((sum, t) => sum + t.value, 0)
  return (
    <div>
      <h3>Orders</h3>
      {plan.trades.length > 0 && (
        <div className="order-summary">
          <span className="badge warning">
            Sell {money(total(sells), 'USD', 0)} · {plural(sells.length, 'order')}
          </span>
          <span className="badge good">
            Buy {money(total(buys), 'USD', 0)} · {plural(buys.length, 'order')}
          </span>
          <span className="badge">Cash after {money(plan.cash_after, 'USD', 0)}</span>
        </div>
      )}
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
    </div>
  )
}
