// Names for the API's schemas, generated from openapi.json into schema.d.ts.
import type { components } from './schema'

type Schemas = components['schemas']

/** Only the admin's account is sent `admin`; it is left out of the API schema. */
export interface AdminSettings {
  /** The yfinance fallback for the admin's own requests. */
  yfinance_fallback: boolean
  /** The yfinance fallback for every account and the screener refresh. */
  yfinance_fallback_for_everyone: boolean
  /** YFINANCE_FALLBACK, set where the server is deployed; read-only here. */
  yfinance_fallback_from_server: boolean
}
export type AdminSettingsUpdate = Partial<
  Pick<AdminSettings, 'yfinance_fallback' | 'yfinance_fallback_for_everyone'>
>
export type Account = Schemas['Account'] & { admin?: AdminSettings }
export type AnalysisList = Schemas['AnalysisList']
export type AnalysisRequest = Schemas['AnalysisRequest']
export type AnalysisResponse = Schemas['AnalysisResponse']
export type AnalysisSummary = Schemas['AnalysisSummary']
export type BacktestList = Schemas['BacktestList']
export type BacktestRequest = Schemas['BacktestRequest']
export type BacktestResponse = Schemas['BacktestResponse']
export type BacktestSummary = Schemas['BacktestSummary']
export type CopulaFitResponse = Schemas['CopulaFitResponse']
export type CustomBacktestRequest = Schemas['CustomBacktestRequest']
export type CustomStrategy = Schemas['CustomStrategy']
export type AiLimits = Schemas['AiLimits']
export type EquityPoint = Schemas['EquityPoint']
export type FactorRegression = Schemas['FactorRegression']
export type FundamentalsResponse = Schemas['FundamentalsResponse']
export type HoldingShares = Schemas['HoldingShares']
export type HoldingsResponse = Schemas['HoldingsResponse']
export type HoldingValue = Schemas['HoldingValue']
export type InvestmentThesis = Schemas['InvestmentThesis']
export type CompanyMetrics = Schemas['CompanyMetrics']
export type AlternativeData = Schemas['AlternativeData']
export type MarketCalendar = Schemas['MarketCalendar']
export type MarketNews = Schemas['MarketNews']
export type MarketNewsCategory = MarketNews['category']
export type MarketSignals = Schemas['MarketSignals']
export type NewsArticle = Schemas['NewsArticle']
export type NewsDay = Schemas['NewsDay']
export type NewsResponse = Schemas['NewsResponse']
export type OHLCVBar = Schemas['OHLCVBar']
export type PerformanceMetrics = Schemas['PerformanceMetrics']
export type PriceHistory = Schemas['PriceHistory']
export type RankedStock = Schemas['RankedStock']
export type RiskSummary = Schemas['RiskSummary']
export type ScreenFilter = Schemas['ScreenFilter']
export type ScreenRequest = Schemas['ScreenRequest']
export type ScreenResponse = Schemas['ScreenResponse']
export type SearchMatch = Schemas['SearchMatch']
export type SearchResponse = Schemas['SearchResponse']
export type SignInProvider = Schemas['SignInProvider']
export type SimulationRequest = Schemas['SimulationRequest']
export type SimulationResponse = Schemas['SimulationResponse']
export type TickerInfo = Schemas['TickerInfo']
export type UniverseStatus = Schemas['UniverseStatus']
export type VolatilityEstimate = Schemas['VolatilityEstimate']

export type ScreenField = ScreenFilter['field']
export type Strategy = NonNullable<BacktestRequest['strategy']>
export type StrategyType = Strategy['type']
export type CodeLanguage = NonNullable<CustomStrategy['language']>
export type AnalysisKind = NonNullable<AnalysisRequest['kind']>
