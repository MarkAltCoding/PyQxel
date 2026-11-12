import { describe, expect, it } from 'vitest'
import { busiestSlot, slotFor, slotSpan } from './nightlyRefresh'

// Checks at 7 past each UTC hour; in November, New York is five hours behind UTC.
const slots = [
  { checks_at: '2026-11-12T00:07:00Z', others: 2 },
  { checks_at: '2026-11-12T01:07:00Z', others: 1 },
  { checks_at: '2026-11-12T02:07:00Z', others: 0 },
]

describe('slotFor', () => {
  it('finds the first check at or after a refresh time in its zone', () => {
    expect(slotFor(slots, '19:00', 'America/New_York')).toBe(slots[0])
    expect(slotFor(slots, '19:07', 'America/New_York')).toBe(slots[0])
    expect(slotFor(slots, '19:10', 'America/New_York')).toBe(slots[1])
    expect(slotFor(slots, '18:10', 'America/Chicago')).toBe(slots[1])
  })

  it('finds none for a time no listed check starts', () => {
    expect(slotFor(slots, '08:00', 'America/New_York')).toBeUndefined()
  })
})

describe('busiestSlot', () => {
  it('picks the check with the most other refreshes, and none when no one else refreshes', () => {
    expect(busiestSlot(slots)).toBe(slots[0])
    expect(busiestSlot([{ checks_at: '2026-11-12T00:07:00Z', others: 0 }])).toBeUndefined()
  })
})

describe('slotSpan', () => {
  it('names the times a check starts, in the zone', () => {
    expect(slotSpan(slots[0]!, 'America/New_York')).toBe('6:08 PM to 7:07 PM')
  })
})
