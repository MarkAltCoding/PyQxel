import { pageNumbers } from '../lib/paging'

interface Props {
  /** The page shown, from 1. */
  page: number
  pageSize: number
  total: number
  onPage: (page: number) => void
  onPageSize: (size: number) => void
  sizes?: readonly number[]
  /** What is being paged through, plural: "headlines". */
  noun: string
}

/** Previous and next buttons, numbered pages, and how many items each page shows, as a
 * search engine pages its results. */
export function Pager({
  page,
  pageSize,
  total,
  onPage,
  onPageSize,
  sizes = [10, 20, 50, 100],
  noun,
}: Props) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  const first = total === 0 ? 0 : (page - 1) * pageSize + 1
  const last = Math.min(page * pageSize, total)
  return (
    <nav className="pager" aria-label={`Pages of ${noun}`}>
      <span className="muted">
        {first}–{last} of {total} {noun}
      </span>
      <span className="pager-pages">
        <button
          type="button"
          className="button ghost small"
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
        >
          Previous
        </button>
        {pageNumbers(page, pages).map((number, index) =>
          number === null ? (
            <span key={`gap-${index}`} className="muted" aria-hidden="true">
              …
            </span>
          ) : (
            <button
              key={number}
              type="button"
              className="button ghost small"
              aria-label={`Page ${number}`}
              aria-current={number === page ? 'page' : undefined}
              onClick={() => onPage(number)}
            >
              {number}
            </button>
          ),
        )}
        <button
          type="button"
          className="button ghost small"
          disabled={page >= pages}
          onClick={() => onPage(page + 1)}
        >
          Next
        </button>
      </span>
      <label className="pager-size">
        <span>Per page</span>
        <select value={pageSize} onChange={(event) => onPageSize(Number(event.target.value))}>
          {sizes.map((size) => (
            <option key={size} value={size}>
              {size}
            </option>
          ))}
        </select>
      </label>
    </nav>
  )
}
