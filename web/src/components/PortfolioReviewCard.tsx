import { useEffect, useId, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { NO_PROGRESS, readAiStream, type AiProgress } from '../api/aiStream'
import {
  exportPortfolioPrompt,
  getPortfolioReview,
  importPortfolioReview,
  streamPortfolioReview,
} from '../api/endpoints'
import type {
  AccountGoals,
  AccountPlan,
  GroupMode,
  InvestorProfile,
  PortfolioSummary,
  PortfolioContext,
  PortfolioAnalysisRequest,
  PortfolioAnalysisResponse,
  PlannedTrade,
  RebalancePlan,
  TargetPosition,
} from '../api/types'
import { dateTime, listed, money, number, percent } from '../lib/format'
import { keepExport, keptExport } from '../lib/exportedPrompts'
import { readStored, writeStored } from '../lib/storage'
import { useReportView } from '../lib/reportView'
import { useWaitChoice } from '../lib/waitForData'
import { useWebSearchChoice } from '../lib/webSearch'
import { badgeTone, SEVERITY_ORDER, SEVERITY_TONE } from '../lib/tones'
import { ContributionLimitsNote } from './AccountSettings'
import { ExternalClaude } from './ExternalClaude'
import { accountTypeLabel, CAPPED_ACCOUNT_TYPES } from '../lib/accounts'
import { NoAnthropicKey } from './NoAnthropicKey'
import { providerKeys } from '../lib/providerKeys'
import { ErrorNotice, Notice } from './Notice'
import { PresetOrCustom } from './PresetOrCustom'
import {
  FinePrint,
  FullText,
  LevelMeter,
  Paragraph,
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
import { paragraphs } from '../lib/reportText'
import { Segmented } from './Segmented'
import { Stat } from './Stat'
import { InfoTip } from './InfoTip'
import { WaitForDataOption } from './WaitForData'
import { AiProgressDetail, WebSearchOption, WebSources } from './WebSearch'
import { Link } from 'react-router'

type Period = NonNullable<PortfolioAnalysisRequest['period']>

/** The usual windows of price history, in months, and the period each is asked for as. */
const HISTORY_PRESETS = [
  { value: 12, label: '1 year' },
  { value: 24, label: '2 years' },
  { value: 60, label: '5 years' },
] as const
const PRESET_PERIOD: Record<number, Period> = { 12: '1y', 24: '2y', 60: '5y' }
const PERIOD_MONTHS: Record<Period, number> = { '1y': 12, '2y': 24, '5y': 60, '10y': 120 }

/** A custom window's limits in months, as the API allows. */
const MIN_HISTORY_MONTHS = 12
const MAX_HISTORY_MONTHS = 120

/** The window a request covers: a preset period, or a custom number of months. */
const historyRequest = (
  months: number,
): Pick<PortfolioAnalysisRequest, 'period' | 'history_months'> =>
  PRESET_PERIOD[months] ? { period: PRESET_PERIOD[months] } : { history_months: months }

/** How much price history a review was written from, in words. */
function historyLabel(response: { period: Period; history_months?: number | null }): string {
  const months = response.history_months ?? PERIOD_MONTHS[response.period]
  if (months % 12 === 0) return `${months / 12} year${months === 12 ? '' : 's'}`
  return `${months} months`
}

/** A share count: whole numbers as they are, fractions to four places. */
/** A share count as brokers take it: whole, or a fraction to four decimals, e.g. 1,204.0625. */
const shareCount = (shares: number) =>
  shares.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 4 })

const FRACTIONAL_KEY = 'pyqxel.fractionalShares'
const ROUND_KEY = 'pyqxel.roundShares'

type Risk = NonNullable<InvestorProfile['risk_tolerance']> | 'unset'

/** Whether a horizon is typed in months, for money needed within a year or two, or years. */
type HorizonUnit = 'months' | 'years'

/** A horizon in years, as the review takes it: 6 months is 0.5. */
function inYears(amount: number, unit: HorizonUnit): number {
  return unit === 'years' ? amount : Math.round((amount / 12) * 10_000) / 10_000
}

const RISKS: { value: Risk; label: string }[] = [
  { value: 'unset', label: 'Not set' },
  { value: 'conservative', label: 'Conservative' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'aggressive', label: 'Aggressive' },
]

type Scope = 'one' | 'group'

const SCOPES: { value: Scope; label: string }[] = [
  { value: 'one', label: 'This portfolio' },
  { value: 'group', label: 'Several together' },
]

const MODES: { value: GroupMode; label: string }[] = [
  { value: 'combined', label: 'As one portfolio' },
  { value: 'separate', label: 'Each with its own plan' },
]

type GoalsMode = 'shared' | 'each'

const GOALS: { value: GoalsMode; label: string }[] = [
  { value: 'shared', label: 'Same goals for all' },
  { value: 'each', label: 'Goals for each account' },
]

/** One account's own goals and cash in a review of several, as typed. */
interface AccountDraft {
  risk: Risk
  horizon: string
  horizonUnit: HorizonUnit
  notes: string
  cash: string
}

const NO_ACCOUNT: AccountDraft = {
  risk: 'unset',
  horizon: '',
  horizonUnit: 'years',
  notes: '',
  cash: '',
}

/** Which portfolios are reviewed together and how, kept with the account. */
interface KeptGroup {
  scope: Scope
  members: number[]
  mode: GroupMode
  goals: GoalsMode
  accounts: Record<string, AccountDraft>
}

const GROUP_KEY = 'pyqxel.reviewGroup'

function keptGroup(): KeptGroup {
  const empty: KeptGroup = {
    scope: 'one',
    members: [],
    mode: 'combined',
    goals: 'shared',
    accounts: {},
  }
  try {
    const kept = JSON.parse(readStored(GROUP_KEY) ?? 'null') as Partial<KeptGroup> | null
    if (!kept || typeof kept !== 'object') return empty
    return {
      scope: kept.scope === 'group' ? 'group' : 'one',
      members: Array.isArray(kept.members)
        ? kept.members.filter((n): n is number => Number.isInteger(n))
        : [],
      mode: kept.mode === 'separate' ? 'separate' : 'combined',
      goals: kept.goals === 'each' ? 'each' : 'shared',
      accounts:
        kept.accounts && typeof kept.accounts === 'object'
          ? Object.fromEntries(
              Object.entries(kept.accounts).map(([number, draft]) => [
                number,
                { ...NO_ACCOUNT, ...(draft as Partial<AccountDraft>) },
              ]),
            )
          : {},
    }
  } catch {
    return empty
  }
}

/** What the person last told the review about themselves, kept with their account so it
 * need not be typed again. */
const PROFILE_KEY = 'pyqxel.reviewProfile'

interface KeptProfile {
  months: number
  risk: Risk
  horizon: string
  horizonUnit: HorizonUnit
  cash: string
  notes: string
}

const NO_PROFILE: KeptProfile = {
  months: 12,
  risk: 'unset',
  horizon: '',
  horizonUnit: 'years',
  cash: '',
  notes: '',
}

function keptProfile(): KeptProfile {
  try {
    const kept = JSON.parse(readStored(PROFILE_KEY) ?? 'null') as Partial<KeptProfile> | null
    if (!kept || typeof kept !== 'object') return NO_PROFILE
    return {
      months:
        Number.isInteger(kept.months) &&
        kept.months! >= MIN_HISTORY_MONTHS &&
        kept.months! <= MAX_HISTORY_MONTHS
          ? kept.months!
          : NO_PROFILE.months,
      risk: RISKS.some((risk) => risk.value === kept.risk) ? kept.risk! : NO_PROFILE.risk,
      horizon: typeof kept.horizon === 'string' ? kept.horizon : '',
      horizonUnit: kept.horizonUnit === 'months' ? 'months' : 'years',
      cash: typeof kept.cash === 'string' ? kept.cash : '',
      notes: typeof kept.notes === 'string' ? kept.notes : '',
    }
  } catch {
    return NO_PROFILE
  }
}

/**
 * Claude's review of the saved portfolio, or of every one together: the latest one stored, and
 * writing a new one, billed to the account's own Anthropic key, so one is only written
 * when asked for.
 */
export function PortfolioReviewCard({
  portfolio,
  hasHoldings,
  portfolios = [],
  stale,
}: {
  /** Which saved portfolio to review. */
  portfolio: number
  hasHoldings: boolean
  /** Every portfolio slot, for choosing several to review together. */
  portfolios?: PortfolioSummary[]
  stale: boolean
}) {
  const queryClient = useQueryClient()
  const [group, setGroup] = useState(keptGroup)
  const keepGroup = (next: Partial<KeptGroup>) => {
    const merged = { ...group, ...next }
    setGroup(merged)
    writeStored(GROUP_KEY, JSON.stringify(merged))
  }
  const eligible = portfolios.filter((p) => p.holdings > 0)
  const eligibleNumbers = eligible.map((p) => p.portfolio)
  const picked = group.members.filter((n) => eligibleNumbers.includes(n))
  // Until some are chosen, every portfolio with holdings is.
  const members = (picked.length > 0 ? picked : eligibleNumbers).sort((a, b) => a - b)
  const grouped = eligible.length > 1 && group.scope === 'group'
  const mode = group.mode
  const separate = grouped && mode === 'separate'
  const each = separate && group.goals === 'each'
  const draftFor = (number: number): AccountDraft => group.accounts[number] ?? NO_ACCOUNT
  const editAccount = (number: number, next: Partial<AccountDraft>) =>
    keepGroup({ accounts: { ...group.accounts, [number]: { ...draftFor(number), ...next } } })
  const toggleMember = (number: number, on: boolean) => {
    const next = on ? [...new Set([...members, number])] : members.filter((n) => n !== number)
    keepGroup({ members: next })
  }
  const nameOf = (number: number) =>
    portfolios.find((p) => p.portfolio === number)?.name ?? `Portfolio ${number}`
  const reviewed = grouped ? members : [portfolio]
  const capped = portfolios.some(
    (p) =>
      reviewed.includes(p.portfolio) && p.account_type && CAPPED_ACCOUNT_TYPES.has(p.account_type),
  )
  // Reviews are stored per portfolio, and those of several together apart from any one,
  // unless each was given its own plan, which then counts as each one's latest.
  const reviewKey = grouped
    ? ['portfolio-review', 'group', mode, members.join(',')]
    : ['portfolio-review', portfolio]
  const exportKey = grouped
    ? `portfolio-group-${mode}-${members.join('-')}`
    : `portfolio-${portfolio}`
  const [kept, setKept] = useState(keptProfile)
  const { months, risk, horizon, horizonUnit, cash, notes } = kept
  const keep = (next: Partial<KeptProfile>) => {
    const merged = { ...kept, ...next }
    setKept(merged)
    writeStored(PROFILE_KEY, JSON.stringify(merged))
  }
  const setMonths = (value: number) => keep({ months: value })
  const setRisk = (value: Risk) => keep({ risk: value })
  const setHorizon = (value: string) => keep({ horizon: value })
  const setHorizonUnit = (value: HorizonUnit) => keep({ horizonUnit: value })
  const setCash = (value: string) => keep({ cash: value })
  const setNotes = (value: string) => keep({ notes: value })
  const idBase = useId()
  const [webSearch, setWebSearch] = useWebSearchChoice()
  const [wait, setWait] = useWaitChoice()
  // Most brokers trade fractions of a share now; the choice is remembered in this browser.
  const [fractional, setFractional] = useState(() => readStored(FRACTIONAL_KEY) !== 'off')
  const [roundShares, setRoundShares] = useState(() => readStored(ROUND_KEY) !== 'off')
  const [progress, setProgress] = useState<AiProgress | null>(null)
  const [error, setError] = useState<unknown>(null)
  const abort = useRef<AbortController | null>(null)
  useEffect(() => () => abort.current?.abort(), [])

  const latest = useQuery({
    queryKey: reviewKey,
    queryFn: () =>
      getPortfolioReview(portfolio, grouped ? { portfolios: members, mode } : undefined),
  })
  const review = latest.data ?? null

  const profile = (): InvestorProfile => ({
    risk_tolerance: risk === 'unset' ? null : risk,
    horizon_years: horizon.trim() ? inYears(Number(horizon), horizonUnit) : null,
    cash_to_invest: cash.trim() ? Number(cash) : 0,
    notes: notes.trim() || null,
    fractional_shares: fractional,
    round_shares: roundShares,
  })

  const accountGoals = (number: number): AccountGoals => {
    const draft = draftFor(number)
    return {
      portfolio: number,
      cash_available: draft.cash.trim() ? Number(draft.cash) : 0,
      ...(each
        ? {
            risk_tolerance: draft.risk === 'unset' ? null : draft.risk,
            horizon_years: draft.horizon.trim()
              ? inYears(Number(draft.horizon), draft.horizonUnit)
              : null,
            notes: draft.notes.trim() || null,
          }
        : {}),
    }
  }
  const scopeRequest = (): PortfolioAnalysisRequest =>
    grouped
      ? {
          portfolios: members,
          group_mode: mode,
          accounts: separate ? members.map(accountGoals) : [],
        }
      : { portfolio }
  const keepResult = (result: PortfolioAnalysisResponse) => {
    queryClient.setQueryData(reviewKey, result)
    // A review giving each account its own plan is each one's latest too.
    if (result.group_mode === 'separate') {
      for (const number of result.portfolios ?? []) {
        void queryClient.invalidateQueries({ queryKey: ['portfolio-review', number] })
      }
    }
  }

  // Outside the app: the prompt's data is kept so the pasted answer is saved with it.
  const getPrompt = async () => {
    const request: PortfolioAnalysisRequest = {
      ...scopeRequest(),
      ...historyRequest(months),
      profile: profile(),
      web_search: webSearch,
      wait_seconds: wait,
    }
    const exported = await exportPortfolioPrompt(request)
    keepExport(exportKey, { request, context: exported.context })
    return exported
  }
  const pasteAnswer = async (response: string) => {
    const kept = keptExport<{ request: PortfolioAnalysisRequest; context: PortfolioContext }>(
      exportKey,
    )
    if (!kept) throw new Error('Get the prompt first: the answer is saved with its data.')
    keepResult(await importPortfolioReview({ ...kept, response }))
  }

  const write = async () => {
    const controller = new AbortController()
    abort.current = controller
    setError(null)
    setProgress(NO_PROGRESS)
    try {
      const response = await streamPortfolioReview(
        {
          ...scopeRequest(),
          ...historyRequest(months),
          profile: profile(),
          web_search: webSearch,
          wait_seconds: wait,
          refresh:
            review !== null && (review.history_months ?? PERIOD_MONTHS[review.period]) === months,
        },
        controller.signal,
      )
      keepResult(await readAiStream<PortfolioAnalysisResponse>(response, setProgress))
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure)
    } finally {
      setProgress(null)
      abort.current = null
    }
  }

  const writing = progress !== null
  const tooFew = grouped && members.length < 2
  const blocked = !(hasHoldings || grouped) || stale || tooFew
  return (
    <section id="ai-review" className="card">
      <div className="card-header">
        <div>
          <div className="heading-with-info">
            <h2>AI portfolio review</h2>
            <InfoTip topic="portfolioReview" />
          </div>
          <div className="subtitle">
            Written by Claude from your saved holdings and your latest{' '}
            <Link to="/screener/picks">Claude’s picks</Link>: where the risk comes from, how well
            the holdings diversify one another, and a specific plan with the orders to carry it out.
            Billed to your Anthropic key.
          </div>
        </div>
        <div className="filter-row">
          {eligible.length > 1 && (
            <div className="field">
              <span className="heading-with-info">
                What to review
                <InfoTip topic="combinedReview" />
              </span>
              <Segmented
                label="What to review"
                value={grouped ? 'group' : 'one'}
                options={SCOPES}
                onChange={(next) => keepGroup({ scope: next })}
              />
            </div>
          )}
          <div className="field">
            <span className="heading-with-info">
              Price history to measure
              <InfoTip topic="reviewHistory" />
            </span>
            <PresetOrCustom
              label="Price history to measure"
              presets={HISTORY_PRESETS}
              value={months}
              onChange={setMonths}
              min={MIN_HISTORY_MONTHS}
              max={MAX_HISTORY_MONTHS}
              unit="months of daily prices"
            />
          </div>
          {writing ? (
            <button type="button" className="button" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          ) : (
            <button
              type="button"
              className="button primary"
              disabled={blocked || !providerKeys().anthropic}
              onClick={() => void write()}
            >
              {review
                ? 'Write a new review'
                : grouped
                  ? 'Review them together'
                  : 'Review my portfolio'}
            </button>
          )}
        </div>
      </div>

      {grouped && (
        <fieldset className="review-group">
          <legend>Portfolios to review together</legend>
          <div className="filter-row" style={{ alignItems: 'center' }}>
            {eligible.map((p) => (
              <label key={p.portfolio} className="checkbox">
                <input
                  type="checkbox"
                  checked={members.includes(p.portfolio)}
                  disabled={writing}
                  onChange={(event) => toggleMember(p.portfolio, event.target.checked)}
                />
                {p.name}
                {p.account_type && (
                  <span className="muted"> · {accountTypeLabel(p.account_type)}</span>
                )}
              </label>
            ))}
          </div>
          {tooFew && <Notice tone="warning">Choose at least two portfolios.</Notice>}
          <div className="filter-row" style={{ alignItems: 'flex-end', marginTop: 8 }}>
            <div className="field">
              <span className="heading-with-info">
                How to review them
                <InfoTip topic="groupReviewMode" />
              </span>
              <Segmented
                label="How to review them"
                value={mode}
                options={MODES}
                onChange={(next) => keepGroup({ mode: next })}
              />
            </div>
            {separate && (
              <div className="field">
                <span className="heading-with-info">
                  Goals
                  <InfoTip topic="accountGoals" />
                </span>
                <Segmented
                  label="Goals"
                  value={group.goals}
                  options={GOALS}
                  onChange={(next) => keepGroup({ goals: next })}
                />
              </div>
            )}
          </div>
        </fieldset>
      )}

      <div style={{ marginBottom: 4 }}>
        <WebSearchOption checked={webSearch} onChange={setWebSearch} disabled={writing} />
        <WaitForDataOption seconds={wait} onChange={setWait} disabled={writing} />
      </div>
      <details style={{ marginBottom: 12 }}>
        <summary>Tailor the plan to your goals (optional)</summary>
        <div className="filter-row" style={{ marginTop: 10, alignItems: 'flex-end' }}>
          {!each && (
            <Segmented label="Risk tolerance" value={risk} options={RISKS} onChange={setRisk} />
          )}
          {!each && (
            <div className="field">
              <label htmlFor={`${idBase}-horizon`}>Horizon</label>
              <div className="field-pair">
                <input
                  id={`${idBase}-horizon`}
                  type="number"
                  min={0}
                  max={horizonUnit === 'years' ? 60 : 720}
                  style={{ width: 80 }}
                  value={horizon}
                  onChange={(e) => setHorizon(e.target.value)}
                />
                <select
                  aria-label="Horizon unit"
                  value={horizonUnit}
                  onChange={(e) => setHorizonUnit(e.target.value as HorizonUnit)}
                >
                  <option value="months">months</option>
                  <option value="years">years</option>
                </select>
              </div>
            </div>
          )}
          <div className="field" style={{ width: 160 }}>
            <span className="heading-with-info">
              New cash to invest
              {separate && <span className="muted">(any account may draw on it)</span>}
            </span>
            <span className="money-input">
              <span aria-hidden="true">$</span>
              <input
                type="number"
                min={0}
                step="any"
                aria-label="New cash to invest, in dollars"
                value={cash}
                onChange={(e) => setCash(e.target.value)}
              />
            </span>
          </div>
          <label className="field" style={{ flex: 1, minWidth: 220 }}>
            <span>{each ? 'Anything else, for all of them' : 'Anything else'}</span>
            <input
              type="text"
              maxLength={1000}
              placeholder="e.g. keep AAPL; no tobacco"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
          <span className="heading-with-info">
            <label className="checkbox">
              <input
                type="checkbox"
                checked={fractional}
                onChange={(e) => {
                  setFractional(e.target.checked)
                  writeStored(FRACTIONAL_KEY, e.target.checked ? null : 'off')
                }}
              />
              My broker trades fractional shares
            </label>
            <InfoTip topic="fractionalBroker" />
          </span>
          {fractional && (
            <span className="heading-with-info">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={roundShares}
                  onChange={(e) => {
                    setRoundShares(e.target.checked)
                    writeStored(ROUND_KEY, e.target.checked ? null : 'off')
                  }}
                />
                Prefer whole shares or round fractions, like 3.5
              </label>
              <InfoTip topic="roundShares" />
            </span>
          )}
        </div>
        {separate && (
          <div className="stack" style={{ gap: 10, marginTop: 12 }}>
            {members.map((number) => (
              <AccountFields
                key={number}
                number={number}
                name={nameOf(number)}
                summary={portfolios.find((p) => p.portfolio === number)}
                draft={draftFor(number)}
                withGoals={each}
                onChange={(next) => editAccount(number, next)}
              />
            ))}
          </div>
        )}
        {capped && (
          <div style={{ marginTop: 12 }}>
            <ContributionLimitsNote />
          </div>
        )}
      </details>

      {!providerKeys().anthropic && !writing && <NoAnthropicKey what="a review" />}
      {error !== null && <ErrorNotice error={error} />}
      {latest.error !== null && <ErrorNotice error={latest.error} />}

      {writing ? (
        <div className="stack" style={{ gap: 10 }} aria-live="polite">
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <div className="spinner" />
            <span>
              {progress.characters > 0
                ? `Writing the review (${progress.characters.toLocaleString()} characters)…`
                : webSearch
                  ? 'Measuring the portfolio, searching the web, and reasoning…'
                  : 'Measuring the portfolio and reasoning…'}
            </span>
          </div>
          <AiProgressDetail progress={progress} />
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
      <ExternalClaude
        getPrompt={getPrompt}
        onPaste={pasteAnswer}
        disabled={writing || blocked}
        webSearch={webSearch}
      />
    </section>
  )
}

/** One account's own cash and, when asked for, its own goals, in a review of several. */
function AccountFields({
  number,
  name,
  summary,
  draft,
  withGoals,
  onChange,
}: {
  number: number
  name: string
  summary: PortfolioSummary | undefined
  draft: AccountDraft
  withGoals: boolean
  onChange: (next: Partial<AccountDraft>) => void
}) {
  const id = `review-account-${number}`
  const type = summary?.account_type
  return (
    <fieldset className="review-account">
      <legend>
        {name}
        {type && <span className="muted"> · {accountTypeLabel(type)}</span>}
        {summary?.contribution_room != null && (
          <span className="muted"> · {money(summary.contribution_room, 'USD', 0)} room left</span>
        )}
      </legend>
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <div className="field" style={{ width: 200 }}>
          <span className="heading-with-info">
            Cash in the account to trade
            <InfoTip topic="accountCash" />
          </span>
          <span className="money-input">
            <span aria-hidden="true">$</span>
            <input
              type="number"
              min={0}
              step="any"
              aria-label={`Cash in ${name} to trade, in dollars`}
              value={draft.cash}
              onChange={(e) => onChange({ cash: e.target.value })}
            />
          </span>
        </div>
        {withGoals && (
          <>
            <Segmented
              label={`Risk tolerance for ${name}`}
              value={draft.risk}
              options={RISKS}
              onChange={(risk) => onChange({ risk })}
            />
            <div className="field">
              <label htmlFor={`${id}-horizon`}>Horizon</label>
              <div className="field-pair">
                <input
                  id={`${id}-horizon`}
                  type="number"
                  min={0}
                  max={draft.horizonUnit === 'years' ? 60 : 720}
                  style={{ width: 80 }}
                  aria-label={`Horizon for ${name}`}
                  value={draft.horizon}
                  onChange={(e) => onChange({ horizon: e.target.value })}
                />
                <select
                  aria-label={`Horizon unit for ${name}`}
                  value={draft.horizonUnit}
                  onChange={(e) => onChange({ horizonUnit: e.target.value as HorizonUnit })}
                >
                  <option value="months">months</option>
                  <option value="years">years</option>
                </select>
              </div>
            </div>
            <label className="field" style={{ flex: 1, minWidth: 200 }}>
              <span>Anything else about this account</span>
              <input
                type="text"
                maxLength={1000}
                aria-label={`Anything else about ${name}`}
                placeholder="e.g. for a house in five years"
                value={draft.notes}
                onChange={(e) => onChange({ notes: e.target.value })}
              />
            </label>
          </>
        )}
      </div>
    </fieldset>
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
      <WebSources research={response.context.web_research} />
      <p className="disclaimer" style={{ marginTop: 16 }}>
        {(response.context.accounts?.length ?? 0) > 1 &&
          `Covers ${listed(response.context.accounts!.map((a) => `${a.account} (${money(a.value, 'USD', 0)})`))} together${response.group_mode === 'separate' ? ', each with its own plan' : ', as one portfolio'} · `}
        {response.context.claude_picks &&
          `Given your Claude’s picks of ${dateTime(response.context.claude_picks.generated_at)} for ${response.context.claude_picks.horizon} · `}
        {response.model} · {dateTime(response.generated_at)} · {historyLabel(response)} of price
        history
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
      label: (report.account_plans ?? []).length > 0 ? 'Plans' : 'Plan',
      count:
        (report.account_plans ?? []).length > 0
          ? (response.account_orders ?? []).reduce((n, o) => n + (o.plan?.trades.length ?? 0), 0)
          : response.plan?.trades.length,
      content: (
        <div className="stack" style={{ gap: 16 }}>
          <div className="plan-summary report-summary">
            {paragraphs(report.plan_summary).map((paragraph) => (
              <Paragraph key={paragraph} text={paragraph} />
            ))}
          </div>
          {(report.account_plans ?? []).length > 0 ? (
            <AccountPlans response={response} />
          ) : (
            <>
              <AllocationChart response={response} />
              {response.plan && <Orders plan={response.plan} />}
            </>
          )}
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
      <div className="report-summary">
        {paragraphs(report.plan_summary).map((paragraph) => (
          <Paragraph key={paragraph} text={paragraph} />
        ))}
      </div>
      {(report.account_plans ?? []).length > 0 ? (
        <AccountPlans response={response} text />
      ) : (
        <>
          <TargetTable response={response} />
          {response.plan && <Orders plan={response.plan} />}
        </>
      )}
      <TextPoints title="Trade-offs and what would change the plan" items={report.considerations} />
      <TextPoints title="Data limitations" items={report.data_limitations} />
    </>
  )
}

/** One position of Claude's plan as shown: cash under the money-market fund it sits in,
 * or as Cash when there is none, never as the ticker CASH. */
interface PlanRow {
  symbol: string
  cash: boolean
  keep: boolean
  now: number
  target: number
  why: string
}

/** Each holding's weight within one account, from how many shares of it the account holds. */
function accountWeights(response: PortfolioAnalysisResponse, account: string): Map<string, number> {
  const values = response.context.holdings.map((h) => {
    const shares = h.accounts?.find((held) => held.account === account)?.shares ?? 0
    return [h.symbol, shares * (h.price ?? 0)] as const
  })
  const total = values.reduce((sum, [, value]) => sum + value, 0)
  return new Map(values.filter(([, v]) => v > 0).map(([symbol, v]) => [symbol, v / total]))
}

function planRows(response: PortfolioAnalysisResponse, plan?: AccountPlan): PlanRow[] {
  const holdings = new Map(response.context.holdings.map((h) => [h.symbol, h]))
  const positions = plan ? plan.target_allocation : response.report.target_allocation
  const weights = plan ? accountWeights(response, plan.account) : null
  const marked = positions.some((t: TargetPosition) => t.cash)
  return positions.map((target: TargetPosition) => {
    const symbol = target.symbol.trim().toUpperCase()
    const held = holdings.get(symbol)
    // Reviews written before cash was marked called it CASH.
    const cash = Boolean(target.cash || held?.cash) || (!marked && symbol === 'CASH' && !held)
    return {
      symbol: cash && !held ? 'Cash' : symbol,
      cash,
      keep: Boolean(target.keep),
      now: weights ? (weights.get(symbol) ?? 0) : (held?.weight ?? 0),
      target: target.target_weight,
      why: target.rationale,
    }
  })
}

/** A position's name: its ticker, the fund cash sits in with a Cash badge, or Cash. */
function PositionName({ row }: { row: PlanRow }) {
  if (!row.cash) return <>{row.symbol}</>
  return (
    <span className="heading-with-info" style={{ gap: 6 }}>
      {row.symbol === 'Cash' ? <span className="cash-position">Cash</span> : row.symbol}
      {row.symbol !== 'Cash' && <span className="badge">Cash</span>}
      <InfoTip topic="planCash" />
    </span>
  )
}

function TargetTable({
  response,
  plan,
}: {
  response: PortfolioAnalysisResponse
  plan?: AccountPlan
}) {
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
          {planRows(response, plan).map((row) => (
            <tr key={row.symbol}>
              <td>
                <PositionName row={row} />
              </td>
              <td className="num">{percent(row.now)}</td>
              <td className="num">
                {percent(row.target)}
                {row.keep && <span className="muted"> · hold</span>}
              </td>
              <td className="wrap">{row.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Each position's weight now and Claude's target, biggest change first, with the
 * reason a click away. */
function AllocationChart({
  response,
  plan,
}: {
  response: PortfolioAnalysisResponse
  plan?: AccountPlan
}) {
  const rows = planRows(response, plan).sort(
    (a, b) => Math.abs(b.target - b.now) - Math.abs(a.target - a.now),
  )
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
          const held = row.keep || Math.abs(change) < 0.0005
          const label = held
            ? 'Hold'
            : `${change > 0 ? '▲ +' : '▼ −'}${(Math.abs(change) * 100).toFixed(1)} pts`
          return (
            <li key={row.symbol}>
              <details>
                <summary>
                  <span className="allocation-symbol">
                    <PositionName row={row} />
                  </span>
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
                  <span className={`allocation-change${held ? ' muted' : ''}`}>{label}</span>
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

/** Each account's own plan, in a review that gave every account one: what it is, how much
 * of the new cash goes into it, its targets, and its orders. */
function AccountPlans({
  response,
  text = false,
}: {
  response: PortfolioAnalysisResponse
  text?: boolean
}) {
  const accounts = new Map((response.context.accounts ?? []).map((a) => [a.account, a]))
  const orders = new Map((response.account_orders ?? []).map((o) => [o.account, o.plan]))
  return (
    <div className="stack" style={{ gap: 20 }}>
      {(response.report.account_plans ?? []).map((plan) => {
        const account = accounts.get(plan.account)
        const placed = orders.get(plan.account)
        return (
          <section key={plan.account} className="account-plan">
            <h3>
              {plan.account}
              {account?.account_type_name && (
                <span className="muted"> · {account.account_type_name}</span>
              )}
            </h3>
            <p className="headline">{plan.headline}</p>
            <div className="report-summary">
              {paragraphs(plan.plan_summary).map((paragraph) => (
                <Paragraph key={paragraph} text={paragraph} />
              ))}
            </div>
            {plan.new_cash > 0 && (
              <p>
                <span className="badge good">
                  Deposit {money(plan.new_cash, 'USD', 0)} of the new cash here
                </span>
              </p>
            )}
            {text ? (
              <TargetTable response={response} plan={plan} />
            ) : (
              <AllocationChart response={response} plan={plan} />
            )}
            {placed && <Orders plan={placed} />}
          </section>
        )
      })}
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
          <span className="badge">
            Cash after{plan.cash_symbol ? ` (in ${plan.cash_symbol})` : ''}{' '}
            {money(plan.cash_after, 'USD', 0)}
          </span>
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
                    <span className="heading-with-info" style={{ gap: 6 }}>
                      <span className={`badge ${trade.action === 'buy' ? 'good' : 'warning'}`}>
                        {trade.action === 'buy' ? 'Buy' : 'Sell'}
                      </span>
                      {trade.fractional && (
                        <>
                          <span className="badge">Fractional</span>
                          <InfoTip topic="fractionalShares" />
                        </>
                      )}
                    </span>
                  </td>
                  <td>{trade.symbol}</td>
                  <td className="num">{trade.shares == null ? '—' : shareCount(trade.shares)}</td>
                  <td className="num">{money(trade.price)}</td>
                  <td className="num">{money(trade.value)}</td>
                  <td>
                    {trade.order_type === 'limit'
                      ? `Limit at ${money(trade.limit_price)}${trade.fractional ? ', day order' : ''}`
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
        {money(plan.cash_after)} would be left as cash
        {plan.cash_symbol ? `, in ${plan.cash_symbol}, which needs no order` : ''}. Prices move:
        check them, and place sells before buys.
      </p>
      {(plan.notes ?? []).map((note) => (
        <div key={note} style={{ marginTop: 8 }}>
          <Notice tone="warning">{note}</Notice>
        </div>
      ))}
    </div>
  )
}
