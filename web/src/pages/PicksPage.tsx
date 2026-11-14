import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { NO_PROGRESS, readAiStream, type AiProgress } from '../api/aiStream'
import {
  deletePicks,
  exportPicksPrompt,
  getLatestPicks,
  getPicks,
  getUniverseStatus,
  importPicks,
  listPicks,
  streamPicks,
} from '../api/endpoints'
import type {
  ClaudePick,
  PicksContext,
  PicksRequest,
  PicksResponse,
  ScreenedStock,
} from '../api/types'
import { ExternalClaude } from '../components/ExternalClaude'
import { InfoTip } from '../components/InfoTip'
import { NoAnthropicKey } from '../components/NoAnthropicKey'
import { ErrorNotice, Notice } from '../components/Notice'
import {
  FinePrint,
  PointColumn,
  ReportTabs,
  Summary,
  Verdict,
  type Tab,
} from '../components/ReportParts'
import { ScreenerTabs } from '../components/ScreenerTabs'
import { Segmented } from '../components/Segmented'
import { AiProgressDetail, WebSearchOption, WebSources } from '../components/WebSearch'
import { compact, dateTime, money, number, percent, shortDate, signedPercent } from '../lib/format'
import { keepExport, keptExport } from '../lib/exportedPrompts'
import type { InfoKey } from '../lib/glossary'
import {
  horizonLabel,
  horizonOf,
  horizonProblem,
  LATEST_PICKS_KEY,
  PICKS_LIST_KEY,
  profileOf,
  storedPicksForm,
  storePicksForm,
  type PicksForm,
  type PicksKinds,
  type PicksRisk,
} from '../lib/picks'
import { providerKeys } from '../lib/providerKeys'
import { badgeTone } from '../lib/tones'
import { useWebSearchChoice } from '../lib/webSearch'

const RISKS: { value: PicksRisk; label: string }[] = [
  { value: 'unset', label: 'Not set' },
  { value: 'conservative', label: 'Conservative' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'aggressive', label: 'Aggressive' },
]

const KINDS: { value: PicksKinds; label: string }[] = [
  { value: '', label: 'Stocks and ETFs' },
  { value: 'stock', label: 'Stocks only' },
  { value: 'etf', label: 'ETFs only' },
]

/** Holding periods one click away; any other is typed. */
const PRESETS: { label: string; years: string; months: string; weeks: string }[] = [
  { label: '1 week', years: '', months: '', weeks: '1' },
  { label: '1 month', years: '', months: '1', weeks: '' },
  { label: '6 months', years: '', months: '6', weeks: '' },
  { label: '1 year', years: '1', months: '', weeks: '' },
  { label: '5 years', years: '5', months: '', weeks: '' },
]

const CONVICTION_TONE = { low: 'warning', medium: 'neutral', high: 'good' } as const

const EXPORT_NAME = 'picks'

/**
 * Claude's picks: the securities Claude would buy now and hold for a period the person
 * chooses, picked from their screener. The latest list is shown, earlier ones can be
 * opened, and a new one is made only when asked for, since it is billed to the account's
 * own Anthropic key.
 */
export function PicksPage() {
  const queryClient = useQueryClient()
  const idBase = useId()
  const [form, setForm] = useState<PicksForm>(storedPicksForm)
  const [webSearch, setWebSearch] = useWebSearchChoice()
  const [progress, setProgress] = useState<AiProgress | null>(null)
  const [error, setError] = useState<unknown>(null)
  // An earlier list opened from the history, shown in place of the latest.
  const [opened, setOpened] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const change = (next: Partial<PicksForm>) => {
    const merged = { ...form, ...next }
    setForm(merged)
    storePicksForm(merged)
  }

  const status = useQuery({ queryKey: ['universe-status'], queryFn: getUniverseStatus })
  const latest = useQuery({ queryKey: LATEST_PICKS_KEY, queryFn: getLatestPicks })
  const history = useQuery({ queryKey: PICKS_LIST_KEY, queryFn: listPicks })
  const earlier = useQuery({
    queryKey: ['picks', opened],
    queryFn: () => getPicks(opened!),
    enabled: opened !== null,
    staleTime: Infinity,
  })
  const shown = opened !== null ? (earlier.data ?? null) : (latest.data ?? null)

  const problem = horizonProblem(form)
  const request = (refresh: boolean): PicksRequest => ({
    profile: profileOf(form),
    web_search: webSearch,
    refresh,
  })
  const keepLatest = (result: PicksResponse) => {
    queryClient.setQueryData(LATEST_PICKS_KEY, result)
    void queryClient.invalidateQueries({ queryKey: PICKS_LIST_KEY })
    setOpened(null)
  }

  const write = async () => {
    const controller = new AbortController()
    abort.current = controller
    setError(null)
    setProgress(NO_PROGRESS)
    try {
      // Asking again for the holding period and risk already shown means new picks, not
      // the stored ones.
      const same =
        latest.data != null &&
        latest.data.context.horizon === horizonLabel(horizonOf(form)) &&
        (latest.data.context.profile.risk_tolerance ?? 'unset') === form.risk
      const response = await streamPicks(request(same), controller.signal)
      keepLatest(await readAiStream<PicksResponse>(response, setProgress))
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure)
    } finally {
      setProgress(null)
      abort.current = null
    }
  }

  // Outside the app: the prompt's data is kept so the pasted answer is saved with it.
  const getPrompt = async () => {
    const asked = request(false)
    const exported = await exportPicksPrompt(asked)
    keepExport(EXPORT_NAME, { request: asked, context: exported.context })
    return exported
  }
  const pasteAnswer = async (response: string) => {
    const kept = keptExport<{ request: PicksRequest; context: PicksContext }>(EXPORT_NAME)
    if (!kept) throw new Error('Get the prompt first: the answer is saved with its data.')
    keepLatest(await importPicks({ ...kept, response }))
  }

  const remove = async (id: string) => {
    setError(null)
    try {
      await deletePicks(id)
      setOpened(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: LATEST_PICKS_KEY }),
        queryClient.invalidateQueries({ queryKey: PICKS_LIST_KEY }),
      ])
    } catch (failure) {
      setError(failure)
    }
  }

  const writing = progress !== null
  const noScreener = status.data?.stocks === 0
  const items = history.data?.items ?? []
  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Claude’s picks</h1>
          <div className="subtitle">
            What Claude would buy now and hold for as long as you say, picked from your screener
          </div>
        </div>
        <ScreenerTabs />
      </header>

      {noScreener && (
        <Notice>
          Claude picks from the screener, which has no securities for your account yet. Turn on the{' '}
          <Link to="/settings#nightly-refresh">nightly screener refresh</Link> in Settings to build
          your own; the picks are available once it has run.
        </Notice>
      )}

      <section id="picks-form" className="card">
        <div className="card-header">
          <div>
            <div className="heading-with-info">
              <h2>What to pick for</h2>
              <InfoTip topic="claudesPicks" />
            </div>
            <div className="subtitle">
              Claude weighs the screener’s leaders on value, growth, quality, momentum, and
              steadiness, and picks five to ten for your holding period, with how much of each to
              buy, why, and when to sell. Billed to your Anthropic key.
            </div>
          </div>
          {writing ? (
            <button type="button" className="button" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          ) : (
            <button
              type="button"
              className="button primary"
              disabled={problem !== null || !providerKeys().anthropic || noScreener}
              onClick={() => void write()}
            >
              {latest.data ? 'Pick again' : 'Get Claude’s picks'}
            </button>
          )}
        </div>

        <div className="filter-row" style={{ alignItems: 'flex-end' }}>
          <fieldset className="horizon-fields">
            <legend className="heading-with-info">
              Hold for
              <InfoTip topic="picksHorizon" />
            </legend>
            <div className="field-pair">
              {(
                [
                  ['years', 'years', 60],
                  ['months', 'months', 720],
                  ['weeks', 'weeks', 3180],
                ] as const
              ).map(([name, unit, max]) => (
                <label key={name} className="horizon-part" htmlFor={`${idBase}-${name}`}>
                  <input
                    id={`${idBase}-${name}`}
                    type="number"
                    inputMode="numeric"
                    min={0}
                    max={max}
                    step={1}
                    aria-label={`Hold for, ${unit}`}
                    aria-invalid={problem !== null ? true : undefined}
                    value={form[name]}
                    onChange={(event) => change({ [name]: event.target.value })}
                  />
                  <span>{unit}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="field">
            <span>Or choose</span>
            <div className="segmented" role="group" aria-label="Common holding periods">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  aria-pressed={
                    form.years === preset.years &&
                    form.months === preset.months &&
                    form.weeks === preset.weeks
                  }
                  onClick={() =>
                    change({ years: preset.years, months: preset.months, weeks: preset.weeks })
                  }
                >
                  {preset.label}
                </button>
              ))}
            </div>
          </div>
        </div>
        {problem !== null && (
          <p className="field-problem" role="alert">
            {problem}
          </p>
        )}

        <div className="filter-row" style={{ marginTop: 12, alignItems: 'flex-end' }}>
          <div className="field">
            <span className="heading-with-info">
              Risk tolerance
              <InfoTip topic="picksRisk" />
            </span>
            <Segmented
              label="Risk tolerance"
              value={form.risk}
              options={RISKS}
              onChange={(risk) => change({ risk })}
            />
          </div>
          <label className="field" style={{ width: 160 }}>
            <span>Amount to invest</span>
            <span className="money-input">
              <span aria-hidden="true">$</span>
              <input
                type="number"
                min={0}
                step="any"
                aria-label="Amount to invest, in dollars"
                value={form.amount}
                onChange={(event) => change({ amount: event.target.value })}
              />
            </span>
          </label>
          <label className="field">
            <span>Pick from</span>
            <select
              value={form.kinds}
              onChange={(event) => change({ kinds: event.target.value as PicksKinds })}
            >
              {KINDS.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span>Anything else</span>
            <input
              type="text"
              maxLength={1000}
              placeholder="e.g. dividends; no tobacco"
              value={form.notes}
              onChange={(event) => change({ notes: event.target.value })}
            />
          </label>
        </div>
        <div style={{ marginTop: 8 }}>
          <WebSearchOption checked={webSearch} onChange={setWebSearch} disabled={writing} />
        </div>

        {!providerKeys().anthropic && !writing && (
          <div style={{ marginTop: 12 }}>
            <NoAnthropicKey what="its picks" />
          </div>
        )}
        {error !== null && (
          <div style={{ marginTop: 12 }}>
            <ErrorNotice error={error} />
          </div>
        )}
        <ExternalClaude
          getPrompt={getPrompt}
          onPaste={pasteAnswer}
          saveLabel="Save picks"
          disabled={writing || problem !== null}
        />
      </section>

      <section id="picks" className="card">
        <div className="card-header">
          <div>
            <h2>{opened !== null ? 'Earlier picks' : 'Latest picks'}</h2>
            {shown && (
              <div className="subtitle">
                To hold for {shown.context.horizon}
                {shown.context.profile.risk_tolerance
                  ? ` · ${shown.context.profile.risk_tolerance} risk`
                  : ''}{' '}
                · made {dateTime(shown.generated_at)}
              </div>
            )}
          </div>
          <div className="filter-row" style={{ alignItems: 'flex-end' }}>
            {items.length > 1 && (
              <label className="field">
                <span>Show</span>
                <select
                  value={opened ?? ''}
                  onChange={(event) => setOpened(event.target.value || null)}
                >
                  <option value="">Latest picks</option>
                  {items.slice(1).map((item) => (
                    <option key={item.id} value={item.id}>
                      {shortDate(item.generated_at)} · {item.horizon}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {shown && (
              <Link
                className="button"
                to={opened !== null ? `/screener?picks=${opened}` : '/screener?picks=latest'}
              >
                Show in the screener
              </Link>
            )}
          </div>
        </div>
        {latest.error !== null && <ErrorNotice error={latest.error} />}
        {earlier.error !== null && <ErrorNotice error={earlier.error} />}
        {writing ? (
          <div className="stack" style={{ gap: 10 }} aria-live="polite">
            <div className="filter-row" style={{ alignItems: 'center' }}>
              <div className="spinner" />
              <span>
                {progress.characters > 0
                  ? `Writing the picks (${progress.characters.toLocaleString()} characters)…`
                  : webSearch
                    ? 'Reading the screener, searching the web, and reasoning…'
                    : 'Reading the screener and reasoning…'}
              </span>
            </div>
            <AiProgressDetail progress={progress} />
          </div>
        ) : shown ? (
          <PicksReportView response={shown} onDelete={shown.id ? remove : undefined} />
        ) : latest.isLoading || earlier.isLoading ? (
          <div className="empty">
            <div className="spinner" style={{ margin: '0 auto' }} />
          </div>
        ) : (
          <div className="empty">
            No picks yet. Say how long you would hold them, then get Claude’s picks.
          </div>
        )}
      </section>
    </div>
  )
}

/** The figures of a pick shown beside its reasons, each explained by its glossary entry. */
const FIGURES: {
  field: keyof ScreenedStock
  label: string
  info: InfoKey
  show: (value: number) => string
  /** Whether it is a company's figure, which an ETF does not have. */
  company?: boolean
}[] = [
  { field: 'price', label: 'Price', info: 'price', show: (v) => money(v) },
  { field: 'market_cap', label: 'Market cap', info: 'marketCap', show: compact, company: true },
  { field: 'pe_ratio', label: 'P/E', info: 'pe', show: (v) => number(v, 1), company: true },
  {
    field: 'fcf_yield',
    label: 'FCF yield',
    info: 'fcfYield',
    show: (v) => percent(v),
    company: true,
  },
  {
    field: 'revenue_growth',
    label: 'Revenue growth',
    info: 'revenueGrowth',
    show: (v) => signedPercent(v),
    company: true,
  },
  { field: 'return_1m', label: '1-month return', info: 'return1m', show: (v) => signedPercent(v) },
  {
    field: 'momentum_12_1',
    label: 'Momentum (12–1)',
    info: 'momentum12_1',
    show: (v) => signedPercent(v),
  },
  { field: 'volatility', label: 'Volatility', info: 'volatility', show: (v) => percent(v) },
]

function PickCard({
  pick,
  rank,
  figures,
  amount,
  scale,
}: {
  pick: ClaudePick
  rank: number
  /** The screener's figures for it when it was picked, when the screener held it. */
  figures: ScreenedStock | undefined
  amount: number | null | undefined
  /** The largest weight among the picks, which fills the bar. */
  scale: number
}) {
  const known = FIGURES.filter(
    (figure) =>
      figures &&
      !(figure.company && figures.asset_type === 'etf') &&
      typeof figures[figure.field] === 'number',
  )
  return (
    <li className="pick-card">
      <div className="pick-head">
        <span className="pick-rank" aria-hidden="true">
          {rank}
        </span>
        <div className="pick-name">
          <Link className="pick-symbol" to={`/stocks/${encodeURIComponent(pick.symbol)}`}>
            {pick.symbol}
          </Link>
          <span className="muted">{pick.name}</span>
        </div>
        <span className="badge">{pick.kind === 'etf' ? 'ETF' : 'Stock'}</span>
        <span className="heading-with-info">
          <span className={`badge ${badgeTone(CONVICTION_TONE[pick.conviction])}`}>
            {pick.conviction} conviction
          </span>
          <InfoTip topic="pickConviction" />
        </span>
      </div>
      <div className="pick-weight">
        <span className="heading-with-info">
          <strong>{percent(pick.weight, 0)}</strong>
          {amount ? <span>· about {money(pick.weight * amount, 'USD', 0)}</span> : null}
          <InfoTip topic="pickWeight" />
        </span>
        <span
          className="pick-weight-bar"
          role="img"
          aria-label={`${percent(pick.weight, 0)} of the money invested`}
        >
          <span style={{ width: `${(pick.weight / scale) * 100}%` }} />
        </span>
      </div>
      <p className="pick-thesis">{pick.thesis}</p>
      <dl className="pick-plan">
        <div>
          <dt>Risk</dt>
          <dd>{pick.risk}</dd>
        </div>
        <div>
          <dt>How to buy</dt>
          <dd>{pick.entry}</dd>
        </div>
        <div>
          <dt>When to sell</dt>
          <dd>{pick.exit}</dd>
        </div>
      </dl>
      {known.length > 0 ? (
        <ul className="pick-figures" aria-label={`The screener’s figures for ${pick.symbol}`}>
          {known.map((figure) => (
            <li key={figure.field}>
              <span className="heading-with-info">
                {figure.label}
                <InfoTip topic={figure.info} />
              </span>
              <strong>{figure.show(figures![figure.field] as number)}</strong>
            </li>
          ))}
        </ul>
      ) : (
        <p className="disclaimer heading-with-info">
          <span>Not in your screener, so it has no figures here.</span>
          <InfoTip topic="pickOutsideScreener" />
        </p>
      )}
    </li>
  )
}

function PicksReportView({
  response,
  onDelete,
}: {
  response: PicksResponse
  onDelete?: (id: string) => void
}) {
  const { report, context } = response
  const figures = new Map((response.securities ?? []).map((stock) => [stock.symbol, stock]))
  const scale = Math.max(0.01, ...report.picks.map((pick) => pick.weight))
  const amount = context.profile.amount
  const tabs: Tab[] = [
    {
      id: 'picks',
      label: 'Picks',
      count: report.picks.length,
      content: (
        <ol className="pick-list">
          {report.picks.map((pick, index) => (
            <PickCard
              key={pick.symbol}
              pick={pick}
              rank={index + 1}
              figures={figures.get(pick.symbol)}
              amount={amount}
              scale={scale}
            />
          ))}
        </ol>
      ),
    },
    {
      id: 'view',
      label: 'Market and approach',
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <div>
            <h3>How the market looks for {context.horizon}</h3>
            <Summary text={report.market_view} />
          </div>
          <div>
            <h3>How these were chosen</h3>
            <Summary text={report.approach} />
          </div>
        </div>
      ),
    },
    {
      id: 'passed-over',
      label: 'Passed over',
      count: report.passed_over.length,
      content: <PointColumn title="Left out, and why" items={report.passed_over} tone="neutral" />,
    },
    {
      id: 'before-buying',
      label: 'Before you buy',
      count: report.considerations.length,
      content: (
        <div className="stack" style={{ gap: 14 }}>
          <PointColumn title="Weigh before buying" items={report.considerations} tone="warning" />
          <FinePrint title="What the data cannot show" items={report.data_limitations} />
        </div>
      ),
    },
  ]
  return (
    <article className="report">
      <Verdict headline={report.headline}>
        <div className="picks-facts">
          <span className="badge">Hold for {context.horizon}</span>
          <span className="badge">
            {context.candidates.length} candidates from{' '}
            {context.universe.securities.toLocaleString()} in{' '}
            {context.universe.own ? 'your screener' : 'the screener'}
            <InfoTip topic="pickCandidates" />
          </span>
          {context.universe.prices_as_of && (
            <span className="badge">Prices as of {shortDate(context.universe.prices_as_of)}</span>
          )}
        </div>
      </Verdict>
      <ReportTabs label="Parts of the picks" tabs={tabs} />
      {context.notice && (
        <div style={{ marginTop: 12 }}>
          <Notice>{context.notice}</Notice>
        </div>
      )}
      <WebSources research={context.web_research} />
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {response.model} · {dateTime(response.generated_at)}
        {response.cached && ' · stored, not billed again'} · {response.disclaimer}
      </p>
      {onDelete && response.id && (
        <button
          type="button"
          className="button ghost small danger"
          onClick={() => onDelete(response.id!)}
        >
          Delete these picks
        </button>
      )}
    </article>
  )
}
