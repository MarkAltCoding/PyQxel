// One function per API route the pages use.
import { api, ApiError, send } from './client'
import type {
  Account,
  AppSignInKey,
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
  PortfolioAnalysisRequest,
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
/** Revokes `key` if signing in issued it; keys made for scripts keep working. */
export const revokeSignIn = (key: string) =>
  api<void>(`${V1}/auth/signout`, { method: 'POST', key })
export const updateLimits = (body: AiLimits) =>
  api<Account>(`${V1}/me/limits`, { method: 'PUT', body })
export const updateAdminSettings = (body: AdminSettingsUpdate) =>
  api<Account>(`${V1}/me/admin`, { method: 'PUT', body })
export const deleteAccount = () => api<void>(`${V1}/me`, { method: 'DELETE' })
export const getNightlyRefresh = () => api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`)
/** The status, with `key_use` saying what a refresh on `keys`, giving `stocks` stocks price
 * histories, would send on each key. */
export const previewNightlyRefresh = (keys: string[], stocks: number) =>
  api<NightlyRefreshStatus>(`${V1}/me/nightly-refresh`, {
    query: { use_key: keys, price_history_stocks: stocks },
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
export const getHistory = (symbol: string, period: string, interval: string) =>
  api<PriceHistory>(`${path(symbol)}/history`, { query: { period, interval } })
export const getVolatility = (symbol: string, period: string, model: 'garch' | 'ewma') =>
  api<VolatilityEstimate>(`${path(symbol)}/volatility`, { query: { period, model, horizon: 21 } })
export const getFactors = (symbol: string, period: string, model: string) =>
  api<FactorRegression>(`${path(symbol)}/factors`, { query: { period, model } })
export const getAlternative = (symbol: string) =>
  api<AlternativeData>(`${path(symbol)}/alternative`)
export const getMarketCalendar = (days: number, minMarketCap: number) =>
  api<MarketCalendar>(`${V1}/markets/calendar`, {
    query: { days, min_market_cap: minMarketCap },
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
/** The latest stored review of a portfolio, or of every one together when `combined`, or
 * null when there is none yet. */
export const getPortfolioReview = async (
  portfolio: number,
  combined = false,
): Promise<PortfolioAnalysisResponse | null> => {
  try {
    return await api<PortfolioAnalysisResponse>(`${V1}/portfolio/analysis`, {
      query: { portfolio, combined },
    })
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}
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
