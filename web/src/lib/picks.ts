// What Claude's picks are tailored to, as the form holds it, kept with the account so it
// need not be entered again; and the shared names of the picks' queries.

import type { PicksProfile } from '../api/types'
import { listed } from './format'
import { readStored, writeStored } from './storage'

/** The query holding the account's newest picks, which the screener's filter also reads. */
export const LATEST_PICKS_KEY = ['picks', 'latest'] as const
/** The query holding the account's stored lists of picks. */
export const PICKS_LIST_KEY = ['picks', 'list'] as const

export type PicksRisk = NonNullable<PicksProfile['risk_tolerance']> | 'unset'
export type PicksKinds = '' | 'stock' | 'etf'

/** The form's fields, numbers as typed. */
export interface PicksForm {
  years: string
  months: string
  weeks: string
  risk: PicksRisk
  amount: string
  kinds: PicksKinds
  notes: string
}

export const DEFAULT_PICKS_FORM: PicksForm = {
  years: '1',
  months: '',
  weeks: '',
  risk: 'unset',
  amount: '',
  kinds: '',
  notes: '',
}

const KEY = 'pyqxel.picksProfile'

/** The longest holding period, in years, as the API allows. */
export const MAX_HORIZON_YEARS = 60

const RISKS: PicksRisk[] = ['unset', 'conservative', 'moderate', 'aggressive']

export function storedPicksForm(): PicksForm {
  try {
    const kept = JSON.parse(readStored(KEY) ?? 'null') as Partial<PicksForm> | null
    if (!kept || typeof kept !== 'object') return DEFAULT_PICKS_FORM
    const text = (value: unknown, fallback: string) =>
      typeof value === 'string' ? value : fallback
    return {
      years: text(kept.years, ''),
      months: text(kept.months, ''),
      weeks: text(kept.weeks, ''),
      risk: RISKS.includes(kept.risk as PicksRisk) ? (kept.risk as PicksRisk) : 'unset',
      amount: text(kept.amount, ''),
      kinds: kept.kinds === 'stock' || kept.kinds === 'etf' ? kept.kinds : '',
      notes: text(kept.notes, ''),
    }
  } catch {
    return DEFAULT_PICKS_FORM
  }
}

export function storePicksForm(form: PicksForm): void {
  writeStored(KEY, JSON.stringify(form))
}

/** A typed count as a whole number, zero when blank; NaN when it is not one. */
const count = (text: string): number => (text.trim() === '' ? 0 : Number(text))

/** The holding period typed, in whole years, months, and weeks. */
export function horizonOf(form: PicksForm): { years: number; months: number; weeks: number } {
  return { years: count(form.years), months: count(form.months), weeks: count(form.weeks) }
}

/** Why the holding period typed cannot be used, or null when it can: it is any mix of
 * whole years, months, and weeks, from one week to sixty years. */
export function horizonProblem(form: PicksForm): string | null {
  const { years, months, weeks } = horizonOf(form)
  const parts = [years, months, weeks]
  if (parts.some((part) => !Number.isInteger(part) || part < 0)) {
    return 'Enter whole numbers of years, months, and weeks.'
  }
  const total = years * 52.18 + months * (52.18 / 12) + weeks
  if (total < 1) return 'Hold for at least one week.'
  if (total > MAX_HORIZON_YEARS * 52.18) return `Hold for at most ${MAX_HORIZON_YEARS} years.`
  return null
}

/** The holding period in words, as the API writes it: "1 year, 6 months, and 2 weeks". */
export function horizonLabel(horizon: { years: number; months: number; weeks: number }): string {
  const part = (amount: number, unit: string) =>
    amount ? [`${amount} ${unit}${amount === 1 ? '' : 's'}`] : []
  return listed([
    ...part(horizon.years, 'year'),
    ...part(horizon.months, 'month'),
    ...part(horizon.weeks, 'week'),
  ])
}

/** The profile the API takes for the form's fields. */
export function profileOf(form: PicksForm): PicksProfile {
  return {
    horizon: horizonOf(form),
    risk_tolerance: form.risk === 'unset' ? null : form.risk,
    amount: form.amount.trim() && Number(form.amount) > 0 ? Number(form.amount) : null,
    asset_types: form.kinds ? [form.kinds] : [],
    notes: form.notes.trim() || null,
  }
}
