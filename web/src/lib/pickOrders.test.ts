import { describe, expect, it } from 'vitest'
import type { ClaudePick } from '../api/types'
import { orderSteps } from './pickOrders'

const PICK: ClaudePick = {
  symbol: 'ACME',
  name: 'Acme',
  kind: 'stock',
  conviction: 'high',
  weight: 0.25,
  thesis: 't',
  risk: 'r',
  entry: 'e',
  exit: 'x',
  order: {
    order_type: 'limit',
    limit_price: 98.5,
    time_in_force: 'gtc',
    good_for_days: 3,
    if_unfilled: 'skip',
    stop_loss: 90,
    take_profit: 120,
    cancel_after: '2026-11-25',
  },
}

describe('orderSteps', () => {
  it('writes a limit order, its contingency, and its exits as Fidelity steps', () => {
    const steps = orderSteps(PICK, 10_000, 100, '2026-12-31T21:00:00Z')

    // $2,500 at $98.50 is 25 whole shares and $37.50 over.
    expect(steps[0]).toMatch(/Buy 25 shares of ACME .* limit order at \$98\.50/)
    expect(steps[0]).toMatch(/Good 'til canceled .* Nov 25, 2026/)
    expect(steps[1]).toMatch(/\$37\.50 of ACME as a fractional share with a day limit order/)
    expect(steps[1]).toMatch(/enter it again each morning through Nov 25, 2026/)
    expect(steps[2]).toMatch(/keep the money in cash/)
    expect(steps[3]).toMatch(/stop-loss\) at \$90\.00.*sell limit order at \$120\.00.*OCO/)
    expect(steps[4]).toMatch(/price alert .* \$90\.00 and below and at \$120\.00 and above/)
    expect(steps[5]).toMatch(/Dec 31, 2026/)
  })

  it('buys less than a share with daily limits alone, and guards it with alerts', () => {
    const steps = orderSteps({ ...PICK, weight: 0.005 }, 10_000, 100, null)

    expect(steps[0]).toMatch(/^Buy \$50\.00 of ACME as a fractional share/)
    expect(steps.some((step) => /OCO/.test(step))).toBe(false)
    expect(steps.some((step) => /price alert/.test(step))).toBe(true)
  })

  it('keeps a pick past the period when Claude says to', () => {
    const steps = orderSteps({ ...PICK, sell_at_end: false }, 10_000, 100, '2026-12-31T21:00:00Z')

    expect(steps.at(-1)).toMatch(/keep holding ACME as “When to sell” says/)
  })

  it('sizes by percentage without an amount, and gives nothing without an order', () => {
    expect(orderSteps(PICK, null, 100, null)[0]).toMatch(/25% of the money/)
    expect(orderSteps({ ...PICK, order: null }, 10_000, 100, null)).toEqual([])
  })
})
