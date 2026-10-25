import { describe, expect, it } from 'vitest'
import { compact, money, percent, signedPercent, timeAgo, tone } from './format'

describe('format', () => {
  it('shows decimals as percentages, grouped', () => {
    expect(percent(0.253)).toBe('25.3%')
    expect(percent(11.685)).toBe('1,168.5%')
    expect(signedPercent(0.048)).toBe('+4.8%')
    expect(signedPercent(-0.2)).toBe('-20.0%')
  })

  it('shows a dash for missing values', () => {
    expect(percent(null)).toBe('—')
    expect(money(undefined)).toBe('—')
    expect(compact(Number.NaN)).toBe('—')
  })

  it('abbreviates large amounts', () => {
    expect(compact(4.9e12)).toBe('$4.9T')
    expect(compact(883.5e9)).toBe('$883.5B')
    expect(compact(12_000_000, '')).toBe('12.0M')
  })

  it('marks gains and losses', () => {
    expect(tone(0.1)).toBe('up')
    expect(tone(-0.1)).toBe('down')
    expect(tone(0)).toBeUndefined()
  })

  it('says how long ago a time was, then the date beyond a week', () => {
    const now = new Date('2026-10-22T15:00:00Z')
    expect(timeAgo('2026-10-22T14:59:40Z', now)).toBe('just now')
    expect(timeAgo('2026-10-22T14:59:00Z', now)).toBe('1 minute ago')
    expect(timeAgo('2026-10-22T12:00:00Z', now)).toBe('3 hours ago')
    expect(timeAgo('2026-10-20T15:00:00Z', now)).toBe('2 days ago')
    expect(timeAgo('2026-10-01T15:00:00Z', now)).toBe('Oct 1')
    expect(timeAgo(null, now)).toBe('—')
  })
})
