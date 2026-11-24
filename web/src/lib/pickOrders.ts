// The orders behind a pick of Claude's, written as the steps to follow at a broker: which
// order ticket to fill in, at what prices, how long to leave it open, what to do if it
// never fills, and the orders that guard the shares once they are bought.
//
// The account is assumed to be at Fidelity, the steps follow its rules, and the page says
// so. Whole shares take every order type: market, limit, stop-limit, stop-loss, good 'til
// canceled (up to 180 days), and conditional orders (OTOCO, one-triggers-a-one-cancels-
// the-other). Fractional shares, bought in dollars (from $1), take only market orders and
// day limit orders during market hours, and no stop or conditional orders. So the picks
// keep their ideal sizes: the whole shares take Claude's order as given, and the fraction
// left over is bought with a day limit at the same price, entered again each morning
// until the order's last day, which is what a good-'til-canceled order would do.

import type { ClaudePick } from '../api/types'
import { money, shortDate } from './format'

type Order = NonNullable<ClaudePick['order']>

/** The smallest fractional order Fidelity takes, in dollars. */
const FIDELITY_MIN_FRACTION = 1

/** How a pick's money splits into whole shares and a fraction, at the order's price or
 * the last price; `null` without an amount or a price, when it is sized in percentages. */
function split(
  pick: ClaudePick,
  order: Order,
  amount: number | null | undefined,
  price: number | null | undefined,
): { shares: number; per: number; fraction: number } | null {
  const per = order.limit_price ?? price
  if (!amount || !per) return null
  const dollars = pick.weight * amount
  const shares = Math.floor(dollars / per)
  const fraction = dollars - shares * per
  return { shares, per, fraction: fraction >= FIDELITY_MIN_FRACTION ? fraction : 0 }
}

function plural(count: number): string {
  return `${count.toLocaleString('en-US')} share${count === 1 ? '' : 's'}`
}

/** The steps to place and look after a pick's orders, in order. */
export function orderSteps(
  pick: ClaudePick,
  amount: number | null | undefined,
  price: number | null | undefined,
  holdingUntil: string | null | undefined,
): string[] {
  const order = pick.order
  if (!order) return []
  const sizes = split(pick, order, amount, price)
  const until = order.cancel_after ? shortDate(order.cancel_after) : null
  const what = sizes
    ? sizes.shares > 0
      ? `${plural(sizes.shares)} of ${pick.symbol} (about ${money(sizes.shares * sizes.per, 'USD', 0)})`
      : null
    : `${Math.round(pick.weight * 100)}% of the money for the picks in ${pick.symbol}`
  const steps: string[] = []
  const lasts =
    order.time_in_force === 'day'
      ? `Time in force: Day, so it cancels itself at the close${until ? ` on ${until}` : ''}.`
      : `Time in force: Good 'til canceled (GTC). If it has not filled by the close${until ? ` on ${until}` : ''}, cancel it.`

  if (what) {
    if (order.order_type === 'market') {
      steps.push(
        `Buy ${what} with a market order${until ? ` at the open on ${until}` : ''}. Time in force: Day. A market order fills at the next price available, which can differ from the last close.`,
      )
    } else if (order.order_type === 'limit' && order.limit_price) {
      steps.push(
        `Buy ${what} with a limit order at ${money(order.limit_price)}: it fills only at that price or lower. ${lasts}`,
      )
    } else if (order.order_type === 'stop_limit' && order.limit_price && order.stop_price) {
      steps.push(
        `Buy ${what} with a stop-limit order: stop ${money(order.stop_price)}, limit ${money(order.limit_price)}. It waits until ${pick.symbol} trades at ${money(order.stop_price)}, then buys at ${money(order.limit_price)} or lower. ${lasts}`,
      )
    }
  }

  if (sizes && sizes.fraction > 0) {
    const dollars = money(sizes.fraction)
    const lead = what ? 'For the rest, buy' : 'Buy'
    if (order.order_type === 'market') {
      steps.push(
        `${lead} ${dollars} of ${pick.symbol} as a fractional share, with a market order in dollars, during market hours.`,
      )
    } else {
      const limit = order.limit_price ?? price ?? 0
      const trigger =
        order.order_type === 'stop_limit' && order.stop_price
          ? ` once ${pick.symbol} has traded at ${money(order.stop_price)}`
          : ''
      steps.push(
        `${lead} ${dollars} of ${pick.symbol} as a fractional share with a day limit order at ${money(limit)}${trigger}. Fidelity takes fractional orders only at market or as day limits, so if it does not fill, enter it again each morning${until ? ` through ${until}` : ''}, as a GTC order would stay open.`,
      )
    }
  }

  if (order.order_type !== 'market') {
    steps.push(
      order.if_unfilled === 'buy_at_market'
        ? `If it never fills, buy at market at the next session’s open instead: owning ${pick.symbol} matters more here than the price.`
        : `If it never fills, do not chase the price: keep the money in cash.`,
    )
  }

  const exits: string[] = []
  if (order.stop_loss)
    exits.push(
      `a sell stop order (a stop-loss) at ${money(order.stop_loss)}, which sells at the next price available if ${pick.symbol} falls that far (in a fast drop, possibly lower)`,
    )
  if (order.take_profit)
    exits.push(`a sell limit order at ${money(order.take_profit)}, to take the gain`)
  const whole = !sizes || sizes.shares > 0
  const shares = sizes && sizes.fraction > 0 ? 'the whole shares' : 'the shares'
  if (whole && exits.length === 1) {
    steps.push(`Once ${shares} are bought, place ${exits[0]}, good 'til canceled.`)
  } else if (whole && exits.length === 2) {
    steps.push(
      `Once ${shares} are bought, place ${exits[0]}, and ${exits[1]}, both good 'til canceled, as a one-cancels-the-other (OCO) order, so when one fills Fidelity cancels the other. (Entered with the buy, the three together are an OTOCO order.)`,
    )
  }
  if (exits.length > 0 && sizes && sizes.fraction > 0) {
    const levels = [
      order.stop_loss ? `${money(order.stop_loss)} and below` : null,
      order.take_profit ? `${money(order.take_profit)} and above` : null,
    ].filter(Boolean)
    steps.push(
      `Fidelity takes no stop or conditional orders on a fractional share, so set a price alert for ${pick.symbol} at ${levels.join(' and at ')}, and sell the fraction by hand when it fires.`,
    )
  }

  if (holdingUntil && pick.sell_at_end === false)
    steps.push(
      `At the end of the holding period, on ${shortDate(holdingUntil)}, keep holding ${pick.symbol} as “When to sell” says, leaving its stop-loss and target in place, and cancel any buy order still open.`,
    )
  else if (holdingUntil)
    steps.push(
      `At the end of the holding period, by the close on ${shortDate(holdingUntil)}, sell what is left and cancel any order still open.`,
    )
  return steps
}
