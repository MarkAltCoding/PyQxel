// Paying for the nightly refresh: the choices Settings offers, and what Stripe's fees
// would take from each, so a user can see why a longer billing period costs less.

import type { BillingInterval, BillingStatus } from '../api/types'
import { isNativeApp } from './native'

export const BILLING_QUERY_KEY = ['billing'] as const

/** The usual top-ups, in cents; any amount from the server's minimum to its maximum works. */
export const TOP_UP_PRESETS: readonly { value: number; label: string }[] = [
  { value: 500, label: '$5' },
  { value: 1000, label: '$10' },
  { value: 2000, label: '$20' },
  { value: 5000, label: '$50' },
]

export const DEFAULT_TOP_UP_CENTS = 1000

export const INTERVAL_CHOICES: readonly { value: BillingInterval; label: string }[] = [
  { value: 'week', label: 'Weekly' },
  { value: 'month', label: 'Monthly' },
  { value: 'year', label: 'Yearly' },
]

/** Days in each billing period, for spreading a month's cost over it. */
const PERIOD_DAYS: Record<BillingInterval, number> = { week: 7, month: 30, year: 365 }

export const INTERVAL_NAMES: Record<BillingInterval, string> = {
  week: 'week',
  month: 'month',
  year: 'year',
}

/** Stripe's fee on one card payment of `cents`, in cents. */
export function cardFee(cents: number, status: BillingStatus): number {
  return status.card_fee_cents + cents * status.card_fee_rate
}

/** The share of a payment of `cents` that Stripe's fee takes: 0.09 for 9%. */
export function feeShare(cents: number, status: BillingStatus): number {
  return cents > 0 ? cardFee(cents, status) / cents : 0
}

export interface PeriodBill {
  interval: BillingInterval
  /** What one period's refreshes cost, in cents, at the last 30 days' pace. */
  usageCents: number
  /** What a card is charged at a time: the period's usage, or Stripe's minimum charge
   * when usage is less, since a smaller bill waits until the usage reaches it. */
  chargeCents: number
  /** The share of each charge Stripe's fee takes. */
  feeShare: number
}

/** What each billing period would charge and lose to fees, at the pace of the last 30
 * days' refreshes; empty until refreshes have cost something. */
export function periodBills(status: BillingStatus): PeriodBill[] {
  const perDayCents = (status.last_30_days_usd * 100) / 30
  if (perDayCents <= 0) return []
  return INTERVAL_CHOICES.map(({ value }) => {
    const usageCents = perDayCents * PERIOD_DAYS[value]
    const chargeCents = Math.max(usageCents, status.min_charge_cents)
    return { interval: value, usageCents, chargeCents, feeShare: feeShare(chargeCents, status) }
  })
}

/** Days the credit lasts at the last 30 days' pace, or null when nothing has been spent. */
export function creditDays(status: BillingStatus): number | null {
  const perDay = status.last_30_days_usd / 30
  if (perDay <= 0 || status.credit_usd <= 0) return null
  return status.credit_usd / perDay
}

/** Where this build lets the user pay: on Stripe's pages from a browser, through a link
 * to the website from the native app, and nowhere from an App Store build, whose rules
 * forbid pointing to purchases outside the store (the build sets VITE_APP_UPDATES=off). */
export function paymentPlace(): 'here' | 'website' | 'none' {
  if (!isNativeApp()) return 'here'
  return import.meta.env.VITE_APP_UPDATES === 'off' ? 'none' : 'website'
}
