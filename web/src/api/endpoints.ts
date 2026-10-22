// One function per API route the pages use.
import { api, send } from './client'
import type {
  Account,
  AnalysisList,
  AnalysisRequest,
  AnalysisResponse,
  BacktestList,
  BacktestRequest,
  BacktestResponse,
  CopulaFitResponse,
  CredentialsUpdate,
  CustomBacktestRequest,
  AiLimits,
  FactorRegression,
  HoldingShares,
  HoldingsResponse,
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
/** Revokes `key` if signing in issued it; keys made for scripts keep working. */
export const revokeSignIn = (key: string) =>
  api<void>(`${V1}/auth/signout`, { method: 'POST', key })
export const updateCredentials = (body: CredentialsUpdate) =>
  api<Account>(`${V1}/me/credentials`, { method: 'PUT', body })
export const updateLimits = (body: AiLimits) =>
  api<Account>(`${V1}/me/limits`, { method: 'PUT', body })
export const deleteAccount = () => api<void>(`${V1}/me`, { method: 'DELETE' })

export const search = (q: string, signal?: AbortSignal) =>
  api<SearchResponse>(`${V1}/search`, { query: { q, limit: 8 }, signal })

export const getTicker = (symbol: string) => api<TickerInfo>(path(symbol))
export const getHistory = (symbol: string, period: string, interval: string) =>
  api<PriceHistory>(`${path(symbol)}/history`, { query: { period, interval } })
export const getVolatility = (symbol: string, period: string, model: 'garch' | 'ewma') =>
  api<VolatilityEstimate>(`${path(symbol)}/volatility`, { query: { period, model, horizon: 21 } })
export const getFactors = (symbol: string, period: string, model: string) =>
  api<FactorRegression>(`${path(symbol)}/factors`, { query: { period, model } })

export const listAnalyses = (symbol: string) =>
  api<AnalysisList>(`${V1}/analyses`, { query: { symbol, limit: 10 } })
export const getAnalysis = (id: string) => api<AnalysisResponse>(`${V1}/analyses/${id}`)
export const streamAnalysis = (symbol: string, body: AnalysisRequest, signal: AbortSignal) =>
  send(`${path(symbol)}/analysis/stream`, { method: 'POST', body, signal })

export const runBacktest = (symbol: string, body: BacktestRequest) =>
  api<BacktestResponse>(`${path(symbol)}/backtest`, { method: 'POST', body })
/** Scores positions a custom strategy computed in the browser; the server never runs its code. */
export const runCustomBacktest = (symbol: string, body: CustomBacktestRequest) =>
  api<BacktestResponse>(`${path(symbol)}/backtest/custom`, { method: 'POST', body })
export const listBacktests = () => api<BacktestList>(`${V1}/backtests`, { query: { limit: 50 } })
export const getBacktest = (id: string) => api<BacktestResponse>(`${V1}/backtests/${id}`)
export const deleteBacktest = (id: string) =>
  api<void>(`${V1}/backtests/${id}`, { method: 'DELETE' })

export const getHoldings = () => api<HoldingsResponse>(`${V1}/portfolio/holdings`)
export const saveHoldings = (holdings: HoldingShares[]) =>
  api<HoldingsResponse>(`${V1}/portfolio/holdings`, { method: 'PUT', body: { holdings } })
export const simulate = (body: SimulationRequest) =>
  api<SimulationResponse>(`${V1}/portfolio/simulate`, { method: 'POST', body })
export const fitCopula = (symbols: string[], period: string) =>
  api<CopulaFitResponse>(`${V1}/portfolio/copula`, { method: 'POST', body: { symbols, period } })

export const screen = (body: ScreenRequest) =>
  api<ScreenResponse>(`${V1}/screener`, { method: 'POST', body })
export const getUniverseStatus = () => api<UniverseStatus>(`${V1}/screener/status`)
