// What a saved report was asked for, in words: the settings and notes the person gave, so a
// list of picks or a review opened again says what it was tailored to.

import type { PicksContext, PortfolioContext } from '../api/types'
import { listed, money } from './format'

const years = (value: number) =>
  value >= 1
    ? `${Number(value.toFixed(1))} ${value === 1 ? 'year' : 'years'}`
    : `${Math.round(value * 12)} months`

/** The settings of a list of picks. */
export function picksAskedFor(context: PicksContext): string[] {
  // Older saved reports may lack parts of what was asked; show what there is.
  const profile: Partial<PicksContext['profile']> = context.profile ?? {}
  const kinds = profile.asset_types?.length
    ? listed(profile.asset_types.map((kind) => (kind === 'etf' ? 'ETFs' : 'stocks')))
    : 'stocks and ETFs'
  return [
    `held for ${context.horizon}`,
    profile.risk_tolerance ? `${profile.risk_tolerance} risk` : 'no risk level given',
    profile.amount ? `${money(profile.amount, 'USD', 0)} to invest` : 'no amount given',
    kinds,
    context.web_research ? 'searched the web' : 'without a web search',
    ...(profile.notes ? [`your notes: “${profile.notes}”`] : []),
  ]
}

/** The settings of a portfolio review, and each account's own where it had them. */
export function reviewAskedFor(context: PortfolioContext): string[] {
  const profile: Partial<PortfolioContext['profile']> = context.profile ?? {}
  const accounts = (context.accounts ?? []).filter(
    (account) => account.risk_tolerance || account.horizon_years || account.notes,
  )
  return [
    profile.risk_tolerance ? `${profile.risk_tolerance} risk` : 'no risk level given',
    profile.horizon_years ? `a ${years(profile.horizon_years)} horizon` : 'no horizon given',
    profile.cash_to_invest
      ? `${money(profile.cash_to_invest, 'USD', 0)} of new cash`
      : 'no new cash',
    profile.fractional_shares
      ? profile.round_shares
        ? 'fractional shares, rounded where close'
        : 'fractional shares'
      : 'whole shares',
    context.web_research ? 'searched the web' : 'without a web search',
    ...(profile.notes ? [`your notes: “${profile.notes}”`] : []),
    ...accounts.map(
      (account) =>
        `${account.account}: ${listed(
          [
            account.risk_tolerance ? `${account.risk_tolerance} risk` : '',
            account.horizon_years ? `a ${years(account.horizon_years)} horizon` : '',
            account.notes ? `“${account.notes}”` : '',
          ].filter(Boolean),
        )}`,
    ),
  ]
}
