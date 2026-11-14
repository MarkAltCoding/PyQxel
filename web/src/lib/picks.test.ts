import { describe, expect, it } from 'vitest'
import {
  DEFAULT_PICKS_FORM,
  horizonLabel,
  horizonOf,
  horizonProblem,
  profileOf,
  storedPicksForm,
  storePicksForm,
  type PicksForm,
} from './picks'

const form = (fields: Partial<PicksForm>): PicksForm => ({
  ...DEFAULT_PICKS_FORM,
  years: '',
  ...fields,
})

describe('the holding period', () => {
  it('is any mix of years, months, and weeks', () => {
    expect(horizonOf(form({ years: '1', months: '6', weeks: '2' }))).toEqual({
      years: 1,
      months: 6,
      weeks: 2,
    })
    expect(horizonLabel({ years: 1, months: 6, weeks: 2 })).toBe('1 year, 6 months, and 2 weeks')
    expect(horizonLabel({ years: 0, months: 0, weeks: 1 })).toBe('1 week')
    expect(horizonLabel({ years: 2, months: 0, weeks: 3 })).toBe('2 years and 3 weeks')
  })

  it('runs from one week to sixty years, in whole numbers', () => {
    expect(horizonProblem(form({ weeks: '1' }))).toBeNull()
    expect(horizonProblem(form({ years: '60' }))).toBeNull()
    expect(horizonProblem(form({}))).toBe('Hold for at least one week.')
    expect(horizonProblem(form({ weeks: '0' }))).toBe('Hold for at least one week.')
    expect(horizonProblem(form({ years: '60', weeks: '1' }))).toBe('Hold for at most 60 years.')
    expect(horizonProblem(form({ months: '1.5' }))).toMatch(/whole numbers/)
    expect(horizonProblem(form({ weeks: '-2' }))).toMatch(/whole numbers/)
  })
})

describe('the profile', () => {
  it('leaves out what was not entered', () => {
    expect(profileOf(form({ weeks: '2' }))).toEqual({
      horizon: { years: 0, months: 0, weeks: 2 },
      risk_tolerance: null,
      amount: null,
      asset_types: [],
      notes: null,
    })
    expect(
      profileOf(
        form({ years: '3', risk: 'aggressive', amount: '5000', kinds: 'etf', notes: ' AI ' }),
      ),
    ).toEqual({
      horizon: { years: 3, months: 0, weeks: 0 },
      risk_tolerance: 'aggressive',
      amount: 5000,
      asset_types: ['etf'],
      notes: 'AI',
    })
  })

  it('is remembered, and an unreadable one falls back to a year', () => {
    expect(storedPicksForm()).toEqual(DEFAULT_PICKS_FORM)
    const kept = form({ months: '3', risk: 'conservative', notes: 'dividends' })

    storePicksForm(kept)

    expect(storedPicksForm()).toEqual(kept)
    window.localStorage.setItem('pyqxel.picksProfile', '{not json')
    expect(storedPicksForm()).toEqual(DEFAULT_PICKS_FORM)
  })
})
