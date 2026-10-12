import type { FactorRegression } from '../api/types'

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
