// Which page numbers a pager shows, and how many items a page holds.

import { readStored } from './storage'

/** Items per page a pager offers, the first the default. */
export const PAGE_SIZES = [10, 20, 50, 100] as const

/** The page size this viewer chose before under `key`, or the default. */
export function storedPageSize(key: string): number {
  const stored = Number(readStored(key))
  return (PAGE_SIZES as readonly number[]).includes(stored) ? stored : PAGE_SIZES[0]
}

/** Pages shown either side of the current one before the rest are elided. */
const AROUND = 2

/** The page numbers to show: the first, the last, and those around `page`, with `null`
 * where a run of pages is left out. */
export function pageNumbers(page: number, pages: number): (number | null)[] {
  const shown: (number | null)[] = []
  for (let number = 1; number <= pages; number++) {
    if (number === 1 || number === pages || Math.abs(number - page) <= AROUND) {
      shown.push(number)
    } else if (shown.at(-1) !== null) {
      shown.push(null)
    }
  }
  return shown
}
