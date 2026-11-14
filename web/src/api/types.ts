// Names for the API's schemas, generated from openapi.json into schema.d.ts.
import type { components } from './schema'

type Schemas = components['schemas']

/** Only the admin's account is sent `admin`; it is left out of the API schema. */
export interface AdminSettings {
  /** The yfinance fallback for the admin's own requests. */
  yfinance_fallback: boolean
  /** YFINANCE_FALLBACK, set where the server is deployed; read-only here. It too reaches
   * the admin's requests alone. */
  yfinance_fallback_from_server: boolean
  /** Whether the whole server uses data sources whose terms do not clearly allow use in a
   * paid product. */
  restricted_sources: boolean
  /** Whether the admin's own requests use them while the server does not. */
  own_restricted_sources: boolean
  /** Whether the whole server offers the size, value, and momentum factors estimated from
   * ETFs when Ken French's library is not used. */
  style_factors: boolean
  /** What turning `restricted_sources` off stops, one source per line. */
  restricted_source_uses: string[]
}
export type AdminSettingsUpdate = Partial<
  Pick<
    AdminSettings,
    'yfinance_fallback' | 'restricted_sources' | 'own_restricted_sources' | 'style_factors'
  >
>
export type Account = Schemas['Account'] & { admin?: AdminSettings }
export type NightlyRefreshSettings = Schemas['NightlyRefreshSettings']
export type BillingStatus = Schemas['BillingStatus']
export type BillingEntry = Schemas['BillingEntry']
export type BillingInterval = Schemas['SubscriptionRequest']['interval']
export type CheckoutLink = Schemas['CheckoutLink']
export type NightlyRefreshStatus = Schemas['NightlyRefreshStatus']
export type NightlyRun = Schemas['NightlyRun']
export type NightlyKeyUse = Schemas['NightlyKeyUse']
export type NightlyTimeSlot = Schemas['NightlyTimeSlot']
export type UsageResponse = Schemas['UsageResponse']
export type ProviderUsage = Schemas['ProviderUsage']
export type UsageLimit = Schemas['UsageLimit']
export type PlanChoice = Schemas['PlanChoice']
export type StoredPrices = Schemas['StoredPrices']
export type PlannedFetch = Schemas['PlannedFetch']
export type UsageEstimate = Schemas['UsageEstimate']
export type ProviderEstimate = Schemas['ProviderEstimate']
export type HistoryShortfall = Schemas['HistoryShortfall']
export type AssistantReply = Schemas['AssistantReply']
export type AnalysisImport = Schemas['AnalysisImport']
export type AnalysisPromptExport = Schemas['AnalysisPromptExport']
export type PortfolioPromptExport = Schemas['PortfolioPromptExport']
export type PortfolioReviewImport = Schemas['PortfolioReviewImport']
export type PromptExport = Schemas['PromptExport']
export type AssistantRequest = Schemas['AssistantRequest']
export type AssistantResponse = Schemas['AssistantResponse']
export type AssistantTurn = Schemas['AssistantTurn']
export type LastRun = Schemas['LastRun']
export type PortfolioAnalysisRequest = Schemas['PortfolioAnalysisRequest']
export type PortfolioAnalysisResponse = Schemas['PortfolioAnalysisResponse']
export type PortfolioContext = Schemas['PortfolioContext']
export type PortfolioReview = Schemas['PortfolioReview']
export type InvestorProfile = Schemas['InvestorProfile']
export type PicksRequest = Schemas['PicksRequest']
export type PicksProfile = Schemas['PicksProfile']
export type PicksHorizon = Schemas['Horizon']
export type PicksResponse = Schemas['PicksResponse']
export type PicksContext = Schemas['PicksContext']
export type PicksReport = Schemas['PicksReport']
export type ClaudePick = Schemas['Pick']
export type PicksList = Schemas['PicksList']
export type PicksSummary = Schemas['PicksSummary']
export type PicksPromptExport = Schemas['PicksPromptExport']
export type PicksImport = Schemas['PicksImport']
export type PicksForReview = Schemas['PicksForReview']
export type ScreenedStock = Schemas['ScreenedStock']
export type WebResearch = Schemas['WebResearch']
export type WebFinding = Schemas['WebFinding']
export type SavedState = Schemas['SavedState']
export type PlannedTrade = Schemas['PlannedTrade']
export type RebalancePlan = Schemas['RebalancePlan']
export type AnalysisContext = Schemas['AnalysisContext']
export type AnalysisList = Schemas['AnalysisList']
export type AnalysisRequest = Schemas['AnalysisRequest']
export type AnalysisResponse = Schemas['AnalysisResponse']
export type AnalysisSummary = Schemas['AnalysisSummary']
export type BacktestList = Schemas['BacktestList']
export type BacktestRequest = Schemas['BacktestRequest']
export type BacktestResponse = Schemas['BacktestResponse']
export type BacktestSummary = Schemas['BacktestSummary']
export type CopulaFitResponse = Schemas['CopulaFitResponse']
export type CrossSectionalBacktestRequest = Schemas['CrossSectionalBacktestRequest']
export type CrossSectionalBacktestResponse = Schemas['CrossSectionalBacktestResponse']
export type RankedAsset = Schemas['RankedAsset']
export type CustomBacktestRequest = Schemas['CustomBacktestRequest']
export type CustomStrategy = Schemas['CustomStrategy']
export type MultiAssetCustomBacktestRequest = Schemas['MultiAssetCustomBacktestRequest']
export type AssetWeight = Schemas['AssetWeight']
export type AiLimits = Schemas['AiLimits']
export type EquityPoint = Schemas['EquityPoint']
export type FactorRegression = Schemas['FactorRegression']
export type FundamentalsResponse = Schemas['FundamentalsResponse']
export type HoldingShares = Schemas['HoldingShares']
export type HoldingsResponse = Schemas['HoldingsResponse']
export type HoldingValue = Schemas['HoldingValue']
export type LeftOutHolding = Schemas['LeftOutHolding']
export type GainWindow = Schemas['HoldingChangesResponse']['window'] & string
export type HoldingChange = Schemas['HoldingChange']
export type HoldingChangesResponse = Schemas['HoldingChangesResponse']
export type HoldingRange = Schemas['HoldingRange']
export type HoldingRangesResponse = Schemas['HoldingRangesResponse']
export type PortfolioList = Schemas['PortfolioList']
export type PortfolioSummary = Schemas['PortfolioSummary']
export type InvestmentThesis = Schemas['InvestmentThesis']
export type RiskFactor = Schemas['RiskFactor']
export type TargetPosition = Schemas['TargetPosition']
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
export type PriceSummary = Schemas['PriceSummary']
export type ScreenFilter = Schemas['ScreenFilter']
export type ScreenRequest = Schemas['ScreenRequest']
export type ScreenResponse = Schemas['ScreenResponse']
export type SearchMatch = Schemas['SearchMatch']
export type SearchResponse = Schemas['SearchResponse']
export type AppSignInKey = Schemas['AppSignInKey']
export type SignInProvider = Schemas['SignInProvider']
export type RenewedKey = Schemas['RenewedKey']
export type PasswordSignInInfo = Schemas['PasswordSignInInfo']
export type PasswordSignedIn = Schemas['PasswordSignedIn']
export type LinkSent = Schemas['LinkSent']
export type LinkAddress = Schemas['LinkAddress']
export type SimulationRequest = Schemas['SimulationRequest']
export type SimulationResponse = Schemas['SimulationResponse']
export type TickerInfo = Schemas['TickerInfo']
export type FundProfile = Schemas['FundProfile']
export type UniverseStatus = Schemas['UniverseStatus']
export type VolatilityEstimate = Schemas['VolatilityEstimate']

export type ScreenField = ScreenFilter['field']
export type Strategy = NonNullable<BacktestRequest['strategy']>
export type StrategyType = Strategy['type']
export type RankingSignal = NonNullable<CrossSectionalBacktestRequest['signal']>
export type RankingSignalType = RankingSignal['type']
export type CodeLanguage = NonNullable<CustomStrategy['language']>
export type AnalysisKind = NonNullable<AnalysisRequest['kind']>
