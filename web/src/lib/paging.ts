// Which page numbers a pager shows.

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
