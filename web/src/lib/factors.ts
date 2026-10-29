import type { FactorRegression } from '../api/types'
import type { InfoKey } from './glossary'

type Factor = FactorRegression['fit']['exposures'][number]['factor']

export const FACTOR_NAMES: Record<Factor, string> = {
  'Mkt-RF': 'Market',
  SMB: 'Size (small minus big)',
  HML: 'Value (high minus low)',
  RMW: 'Profitability',
  CMA: 'Investment',
  Mom: 'Momentum',
}

/** One-word names, for chart labels where width is short. */
export const FACTOR_SHORT_NAMES: Record<Factor, string> = {
  'Mkt-RF': 'Market',
  SMB: 'Size',
  HML: 'Value',
  RMW: 'Profitability',
  CMA: 'Investment',
  Mom: 'Momentum',
}

/** The glossary entry explaining each factor's beta. */
export const FACTOR_INFO: Record<Factor, InfoKey> = {
  'Mkt-RF': 'marketBeta',
  SMB: 'sizeBeta',
  HML: 'valueBeta',
  RMW: 'profitabilityBeta',
  CMA: 'investmentBeta',
  Mom: 'momentumBeta',
}
