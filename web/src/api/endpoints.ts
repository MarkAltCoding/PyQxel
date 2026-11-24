// One function per API route the pages use.
import { api, ApiError, send } from './client'
import type {
  Account,
  PicksScheduleSettings,
  PicksScheduleStatus,
  PortfolioReviewList,
  ScheduleConflictCheck,
  ScheduleConflicts,
  AccountSettings,
  ContributionLimits,
  GroupMode,
  PortfolioSummary,
  AppSignInKey,
  LinkAddress,
  RenewedKey,
  LinkSent,
  PasswordSignedIn,
  PasswordSignInInfo,
  BillingInterval,
  BillingStatus,
  CheckoutLink,
  NightlyRefreshSettings,
  NightlyRefreshStatus,
  PlannedFetch,
  UsageEstimate,
  UsageResponse,
  AnalysisImport,
  AnalysisPromptExport,
  AdminSettingsUpdate,
  AssistantRequest,
  AssistantResponse,
  AnalysisList,
  AnalysisRequest,
  AnalysisResponse,
  BacktestList,
  BacktestRequest,
  BacktestResponse,
  CompanyMetrics,
  CopulaFitResponse,
  CrossSectionalBacktestRequest,
  CrossSectionalBacktestResponse,
  CustomBacktestRequest,
  MultiAssetCustomBacktestRequest,
  AiLimits,
  AlternativeData,
  MarketCalendar,
  MarketNews,
  MarketNewsCategory,
  FactorRegression,
  HoldingShares,
  HoldingsResponse,
  GainWindow,
  HoldingChangesResponse,
  HoldingRangesResponse,
  PortfolioList,
  MarketSignals,
  NewsResponse,
  PicksImport,
  PicksBenchmark,
  PicksList,
  PicksPerformance,
  SavedPicksList,
  PicksPromptExport,
  PicksCandidateList,
  FeatureUsage,
  PicksRequest,
  PicksResponse,
  SavedState,
  PortfolioAnalysisRequest,
  WaitPlans,
  PortfolioAnalysisResponse,
  PortfolioPromptExport,
  PortfolioReviewImport,
  PromptExport,
  PriceHistory,
  ScreenRequest,
  ScreenResponse,
  SearchResponse,
  SignInProvider,
  SimulationRequest,
  SimulationResponse,
  TickerInfo,
  FundProfile,
  UniverseStatus,
  VolatilityEstimate,
} from './types'

const V1 = '/api/v1'
const path = (symbol: string) => `${V1}/stocks/${encodeURIComponent(symbol)}`

export const getAccount = (key?: string) => api<Account>(`${V1}/me`, { key })
export const getSignInProviders = () => api<SignInProvider[]>(`${V1}/auth/providers`)
/** Trades the one-time code a sign-in handed the native app, and its verifier, for a key. */
export const collectAppSignIn = (code: string, verifier: string) =>
  api<AppSignInKey>(`${V1}/auth/app/collect`, { method: 'POST', body: { code, verifier } })
/** Whether this server offers signing in with an email and password. */
export const getPasswordSignIn = () => api<PasswordSignInInfo>(`${V1}/auth/password`)
/** Emails `email` a link to choose a password, which makes the account if it has none. */
export const sendPasswordLink = (email: string) =>
  api<LinkSent>(`${V1}/auth/password/link`, { method: 'POST', body: { email } })
/** The address an emailed link was sent to, without using the link up. */
export const checkPasswordLink = (token: string) =>
  api<LinkAddress>(`${V1}/auth/password/check`, { method: 'POST', body: { token } })
/** Sets the password an emailed link was sent for, and signs in. */
export const setPassword = (token: string, password: string) =>
  api<PasswordSignedIn>(`${V1}/auth/password/set`, { method: 'POST', body: { token, password } })
/** Signs in with an email and password; `app` names the native app's key as its own. */
export const signInWithPassword = (email: string, password: string, app: boolean) =>
  api<PasswordSignedIn>(`${V1}/auth/password/sign-in`, {
    method: 'POST',
    body: { email, password, app },
  })
/** A new sign-in key in place of `key` once it is a day old, or `null` when not due. */
export const renewSignIn = (key: string) =>
  api<RenewedKey>(`${V1}/auth/renew`, { method: 'POST', key })
/** Revokes `key` if signing in issued it; keys made for scripts keep working. */
export const revokeSignIn = (key: string) =>
  api<void>(`${V1}/auth/signout`, { method: 'POST', key })
export const updateLimits = (body: AiLimits) =>
  api<Account>(`${V1}/me/limits`, { method: 'PUT', body })
export const updateAdminSettings = (body: AdminSettingsUpdate) =>
  api<Account>(`${V1}/me/admin`, { method: 'PUT', body })
export const deleteAccount = () => api<void>(`${V1}/me`, { method: 'DELETE' })
export const getBilling = () => api<BillingStatus>(`${V1}/me/billing`)

export const buyCredit = (amountCents: number) =>
  api<CheckoutLink>(`${V1}/me/billing/top-up`, {
    method: 'POST',
    body: { amount_cents: amountCents },
  })

export const startSubscription = (interval: BillingInterval) =>
  api<CheckoutLink>(`${V1}/me/billing/subscription`, { method: 'POST', body: { interval } })

export const openBillingPortal = () =>
  api<CheckoutLink>(`${V1}/me/billing/portal`, { method: 'POST' })

export const getNightlyRefresh = () => api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`)
/** The status, with `key_use` saying what a refresh on `keys`, with each price-history key
 * fetching up to its limit in `limits` (KEY:COUNT or KEY:all), would send on each key. */
export const previewNightlyRefresh = (
  keys: string[],
  limits: string[],
  universes: string[],
  customSymbols: string[],
) =>
  api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`, {
    query: {
      use_key: keys,
      price_limit: limits,
      universe: universes,
      custom_symbol: customSymbols,
    },
  })

/** Each key's plan usage; `plans` are `provider:plan` choices for keys not on a free plan. */
export const getUsage = (plans: string[]) =>
  api<UsageResponse>(`${V1}/usage`, { query: { plan: plans } })
/** About how many requests fetching `fetches` would send on each key. Fetches nothing. */
export const estimateUsage = (fetches: PlannedFetch[], plans: Record<string, string>) =>
  api<UsageEstimate>(`${V1}/usage/estimate`, { method: 'POST', body: { fetches, plans } })
/** Deletes every price history PyQxel keeps for the user. */
export const deleteStoredPrices = () =>
  send(`${V1}/usage/stored-prices`, { method: 'DELETE' }).then(() => undefined)
/** Opts in, or changes the time; the keys this request carries are stored, encrypted. */
export const enableNightlyRefresh = (body: NightlyRefreshSettings) =>
  api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`, { method: 'PUT', body })
/** Opts out; the stored keys are deleted at once. */
export const disableNightlyRefresh = () =>
  api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`, { method: 'DELETE' })

export const search = (q: string, signal?: AbortSignal) =>
  api<SearchResponse>(`${V1}/search`, { query: { q, limit: 8 }, signal })

export const getTicker = (symbol: string) => api<TickerInfo>(path(symbol))
export const getFund = (symbol: string) => api<FundProfile>(`${path(symbol)}/fund`)
export const getHistory = (symbol: string, period: string, interval: string) =>
  api<PriceHistory>(`${path(symbol)}/history`, { query: { period, interval } })
export const getVolatility = (symbol: string, period: string, model: 'garch' | 'ewma') =>
  api<VolatilityEstimate>(`${path(symbol)}/volatility`, { query: { period, model, horizon: 21 } })
export const getFactors = (symbol: string, period: string, model: string) =>
  api<FactorRegression>(`${path(symbol)}/factors`, { query: { period, model } })
export const getAlternative = (symbol: string) =>
  api<AlternativeData>(`${path(symbol)}/alternative`)
/** The smallest company whose earnings the calendar lists when a ceiling is chosen, so
 * "less than" does not list thousands of tiny companies. */
export const CALENDAR_FLOOR = 1e9

/** The calendar of the next `days` days, with the earnings of companies worth at least
 * `marketCap`, or, when `below`, worth from `CALENDAR_FLOOR` up to less than it. */
export const getMarketCalendar = (days: number, marketCap: number, below = false) =>
  api<MarketCalendar>(`${V1}/markets/calendar`, {
    query: below
      ? { days, min_market_cap: CALENDAR_FLOOR, max_market_cap: marketCap }
      : { days, min_market_cap: marketCap },
  })
export const getMarketNews = (category: MarketNewsCategory) =>
  api<MarketNews>(`${V1}/markets/news`, { query: { category } })
export const getMetrics = (symbol: string) => api<CompanyMetrics>(`${path(symbol)}/metrics`)
export const getSignals = (symbol: string) => api<MarketSignals>(`${path(symbol)}/signals`)
export const getNews = (symbol: string) =>
  api<NewsResponse>(`${path(symbol)}/news`, { query: { limit: 200 } })

export const listAnalyses = (symbol: string) =>
  api<AnalysisList>(`${V1}/analyses`, { query: { symbol, limit: 10 } })
export const getAnalysis = (id: string) => api<AnalysisResponse>(`${V1}/analyses/${id}`)
export const streamAnalysis = (symbol: string, body: AnalysisRequest, signal: AbortSignal) =>
  send(`${path(symbol)}/analysis/stream`, { method: 'POST', body, signal })
// Claude outside the app: the prompt to paste there, and the answer brought back.
export const exportAnalysisPrompt = (symbol: string, body: AnalysisRequest) =>
  api<AnalysisPromptExport>(`${path(symbol)}/analysis/prompt`, { method: 'POST', body })
export const importAnalysis = (symbol: string, body: AnalysisImport) =>
  api<AnalysisResponse>(`${path(symbol)}/analysis/import`, { method: 'POST', body })

export const runBacktest = (symbol: string, body: BacktestRequest) =>
  api<BacktestResponse>(`${path(symbol)}/backtest`, { method: 'POST', body })
/** Scores positions a custom strategy computed in the browser; the server never runs its code. */
export const runCustomBacktest = (symbol: string, body: CustomBacktestRequest) =>
  api<BacktestResponse>(`${path(symbol)}/backtest/custom`, { method: 'POST', body })
/** Ranks a universe of stocks against one another; the result is not stored. */
export const runCrossSectionalBacktest = (body: CrossSectionalBacktestRequest) =>
  api<CrossSectionalBacktestResponse>(`${V1}/backtests/cross-sectional`, { method: 'POST', body })
/** Scores weights a custom strategy set across several symbols in the browser. */
export const runMultiAssetCustomBacktest = (body: MultiAssetCustomBacktestRequest) =>
  api<BacktestResponse>(`${V1}/backtests/custom`, { method: 'POST', body })
export const listBacktests = () => api<BacktestList>(`${V1}/backtests`, { query: { limit: 50 } })
export const getBacktest = (id: string) => api<BacktestResponse>(`${V1}/backtests/${id}`)
export const deleteBacktest = (id: string) =>
  api<void>(`${V1}/backtests/${id}`, { method: 'DELETE' })

export const getPortfolios = () => api<PortfolioList>(`${V1}/portfolio/portfolios`)
export const getHoldings = (portfolio: number) =>
  api<HoldingsResponse>(`${V1}/portfolio/holdings`, { query: { portfolio } })
/** The saved symbols and shares alone, without pricing them, which asks no provider. */
export const getSavedHoldings = (portfolio: number) =>
  api<HoldingsResponse>(`${V1}/portfolio/holdings`, { query: { portfolio, priced: false } })
/** Each holding's gain or loss over a preset window, or since a custom first day. */
export const getHoldingChanges = (symbols: string[], window: GainWindow | { since: string }) =>
  api<HoldingChangesResponse>(`${V1}/portfolio/changes`, {
    query: {
      symbols: symbols.join(','),
      ...(typeof window === 'string' ? { window } : { since: window.since }),
    },
  })
/** Each holding's lowest and highest price over the last 52 weeks. */
export const getHoldingRanges = (symbols: string[]) =>
  api<HoldingRangesResponse>(`${V1}/portfolio/ranges`, {
    query: { symbols: symbols.join(',') },
  })
/** The latest stored review of a portfolio, or of several together when `group` names
 * them and how they were reviewed, or null when there is none yet. */
export const getPortfolioReview = async (
  portfolio: number,
  group?: { portfolios: number[]; mode: GroupMode },
): Promise<PortfolioAnalysisResponse | null> => {
  try {
    return await api<PortfolioAnalysisResponse>(`${V1}/portfolio/analysis`, {
      query: group ? { portfolios: group.portfolios.map(String), mode: group.mode } : { portfolio },
    })
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}
/** Says what kind of account a portfolio is, and how much more may go into it. */
export const saveAccountSettings = (portfolio: number, body: AccountSettings) =>
  api<PortfolioSummary>(`${V1}/portfolio/account`, { method: 'PUT', query: { portfolio }, body })
/** The IRS's yearly contribution limits for this tax year, and the next once published. */
export const getContributionLimits = () =>
  api<ContributionLimits>(`${V1}/portfolio/contribution-limits`)
export const streamPortfolioReview = (body: PortfolioAnalysisRequest, signal: AbortSignal) =>
  send(`${V1}/portfolio/analysis/stream`, { method: 'POST', body, signal })
export const exportPortfolioPrompt = (body: PortfolioAnalysisRequest) =>
  api<PortfolioPromptExport>(`${V1}/portfolio/analysis/prompt`, { method: 'POST', body })
export const importPortfolioReview = (body: PortfolioReviewImport) =>
  api<PortfolioAnalysisResponse>(`${V1}/portfolio/analysis/import`, { method: 'POST', body })
export const exportAssistantPrompt = (body: AssistantRequest) =>
  api<PromptExport>(`${V1}/backtests/assistant/prompt`, { method: 'POST', body })
export const importAssistantReply = (response: string) =>
  api<AssistantResponse>(`${V1}/backtests/assistant/import`, {
    method: 'POST',
    body: { response },
  })
/** Asks the strategy coding assistant; the conversation is sent each time, never stored. */
export const streamAssistant = (body: AssistantRequest, signal: AbortSignal) =>
  send(`${V1}/backtests/assistant/stream`, { method: 'POST', body, signal })
export const saveHoldings = (portfolio: number, holdings: HoldingShares[], name?: string) =>
  api<HoldingsResponse>(`${V1}/portfolio/holdings`, {
    method: 'PUT',
    query: { portfolio },
    body: { holdings, name: name ?? null },
  })
export const simulate = (body: SimulationRequest) =>
  api<SimulationResponse>(`${V1}/portfolio/simulate`, { method: 'POST', body })
export const fitCopula = (symbols: string[], period: string, maxHistoryCut: number | null) =>
  api<CopulaFitResponse>(`${V1}/portfolio/copula`, {
    method: 'POST',
    body: { symbols, period, max_history_cut: maxHistoryCut },
  })

export const screen = (body: ScreenRequest) =>
  api<ScreenResponse>(`${V1}/screener`, { method: 'POST', body })
export const getUniverseStatus = () => api<UniverseStatus>(`${V1}/screener/status`)

/** The account's newest Claude's Picks, or null when it has none yet. */
export const getLatestPicks = async (): Promise<PicksResponse | null> => {
  try {
    return await api<PicksResponse>(`${V1}/picks/latest`)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}
/** The account's stored lists of picks, newest first, without their reasons. */
/** Claude's Picks made on a schedule: what is set, when each runs next, and the last run. */
export const getPicksSchedule = () => api<PicksScheduleStatus>(`${V1}/me/picks-schedule`)
export const savePicksSchedule = (body: PicksScheduleSettings) =>
  api<PicksScheduleStatus>(`${V1}/me/picks-schedule`, { method: 'PUT', body })
export const deletePicksSchedule = () =>
  api<PicksScheduleStatus>(`${V1}/me/picks-schedule`, { method: 'DELETE' })
/** Where scheduled picks would start during the screener refresh, before either is saved. */
export const checkScheduleConflicts = (body: ScheduleConflictCheck) =>
  api<ScheduleConflicts>(`${V1}/me/schedule-conflicts`, { method: 'POST', body })
/** The stored portfolio reviews, newest first, each with what it was tailored to. */
export const listPortfolioReviews = () =>
  api<PortfolioReviewList>(`${V1}/portfolio/analyses`, { query: { limit: 30 } })
export const getStoredPortfolioReview = (id: string) =>
  api<PortfolioAnalysisResponse>(`${V1}/portfolio/analyses/${id}`)
export const deletePortfolioReview = (id: string) =>
  api<void>(`${V1}/portfolio/analyses/${id}`, { method: 'DELETE' })
export const listPicks = () => api<PicksList>(`${V1}/picks`, { query: { limit: 20 } })
export const getPicks = (id: string) => api<PicksResponse>(`${V1}/picks/${id}`)
/** The stored lists of picks, newest first, each with how it has done against ``benchmark``. */
export const getSavedPicks = (benchmark: PicksBenchmark) =>
  api<SavedPicksList>(`${V1}/picks/performance`, { query: { benchmark } })
/** How one stored list of picks has done, pick by pick and day by day. */
export const getPicksPerformance = (id: string, benchmark: PicksBenchmark) =>
  api<PicksPerformance>(`${V1}/picks/${id}/performance`, { query: { benchmark } })
export const deletePicks = (id: string) => api<void>(`${V1}/picks/${id}`, { method: 'DELETE' })
export const streamPicks = (body: PicksRequest, signal: AbortSignal) =>
  send(`${V1}/picks/stream`, { method: 'POST', body, signal })
/** Today's requests on each key by each part of the app, against the daily limits set. */
export const getFeatureUsage = () => api<FeatureUsage>(`${V1}/usage/features`)
/** The securities the next picks would weigh, from the stored screener alone. */
export const getPicksCandidates = (body: PicksRequest) =>
  api<PicksCandidateList>(`${V1}/picks/candidates`, { method: 'POST', body })
export const exportPicksPrompt = (body: PicksRequest) =>
  api<PicksPromptExport>(`${V1}/picks/prompt`, { method: 'POST', body })
/** What each wait would gather for a list of picks, by group; fetches nothing. */
export const getPicksWaitPlans = (body: PicksRequest) =>
  api<WaitPlans>(`${V1}/picks/wait-plans`, { method: 'POST', body })
/** What each wait would gather for a portfolio review, by group; fetches nothing. */
export const getReviewWaitPlans = (body: PortfolioAnalysisRequest) =>
  api<WaitPlans>(`${V1}/portfolio/analysis/wait-plans`, { method: 'POST', body })
/** What each wait would gather for a stock report; fetches nothing. */
export const getAnalysisWaitPlans = (symbol: string) =>
  api<WaitPlans>(`${path(symbol)}/analysis/wait-plans`)
export const importPicks = (body: PicksImport) =>
  api<PicksResponse>(`${V1}/picks/import`, { method: 'POST', body })

/** Everything the app has saved to the account: drafts, screens, answers, and choices. */
export const getSavedState = () => api<SavedState>(`${V1}/me/saved`)
/** Saves each named value to the account, and forgets those given as null. */
export const putSavedState = (items: Record<string, string | null>, keepalive = false) =>
  api<SavedState>(`${V1}/me/saved`, { method: 'PUT', body: { items }, keepalive })
