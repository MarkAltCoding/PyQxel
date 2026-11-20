import { useState } from 'react'
import { Link } from 'react-router'
import { useQuery } from '@tanstack/react-query'
import { getPicksCandidates } from '../api/endpoints'
import type { PicksCandidateRow, PicksRequest } from '../api/types'
import { listed } from '../lib/format'
import { storedPageSize } from '../lib/paging'
import { MAX_ADDED_SYMBOLS } from '../lib/picks'
import { writeStored } from '../lib/storage'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'
import { Pager } from './Pager'
import { SearchBox } from './SearchBox'

const PAGE_SIZE_KEY = 'pyqxel.picksCandidatesPageSize'

/** Whether a row matches the text searched for, by ticker, name, sector, or measure. */
function matches(row: PicksCandidateRow, text: string): boolean {
  const wanted = text.trim().toLowerCase()
  if (!wanted) return true
  return [row.symbol, row.name, row.sector ?? '', ...row.lenses].some((part) =>
    part.toLowerCase().includes(wanted),
  )
}

/**
 * Tickers the person adds to the screener's leaders for the next picks, and every
 * security those picks would weigh, searchable. Claude is given an added ticker as it is
 * given any other candidate, with the screener's figures for it; one the screener does
 * not hold is left out, with where to add it to the screener.
 */
export function PicksCandidates({
  added,
  onChange,
  request,
  disabled = false,
}: {
  added: string[]
  onChange: (next: string[]) => void
  /** The request the next picks would be made with. */
  request: () => PicksRequest
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(() => storedPageSize(PAGE_SIZE_KEY))
  const asked = request()
  const kinds = asked.profile?.asset_types ?? []
  const candidates = useQuery({
    queryKey: ['picks-candidates', kinds, added],
    queryFn: () => getPicksCandidates({ profile: asked.profile, added_symbols: added }),
    enabled: open || added.length > 0,
    staleTime: 5 * 60_000,
  })
  const rows = (candidates.data?.candidates ?? []).filter((row) => matches(row, text))
  const pages = Math.max(1, Math.ceil(rows.length / pageSize))
  const current = Math.min(page, pages)
  const shown = rows.slice((current - 1) * pageSize, current * pageSize)
  const missing = candidates.data?.missing ?? []

  return (
    <div className="stack" style={{ gap: 6, marginTop: 8 }}>
      <div className="heading-with-info">
        <span className="disclaimer">Also weigh these tickers</span>
        <InfoTip topic="picksAdded" />
      </div>
      {added.length > 0 && (
        <ul className="filter-row" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {added.map((symbol) => (
            <li key={symbol} className="chip">
              <span>{symbol}</span>
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${symbol}`}
                disabled={disabled}
                onClick={() => onChange(added.filter((other) => other !== symbol))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {added.length < MAX_ADDED_SYMBOLS ? (
        <div style={{ maxWidth: 360 }}>
          <SearchBox
            label="Add a ticker for Claude to weigh"
            placeholder="Add a ticker or company"
            clearOnSelect
            onSelect={(symbol) => {
              const upper = symbol.toUpperCase()
              if (!added.includes(upper)) onChange([...added, upper])
            }}
          />
        </div>
      ) : (
        <span className="disclaimer">
          That is the most, {MAX_ADDED_SYMBOLS}; remove one to add another.
        </span>
      )}
      {missing.length > 0 && (
        <Notice tone="warning">
          {listed(missing)} {missing.length === 1 ? 'is' : 'are'} not in{' '}
          {candidates.data?.own ? 'your screener' : 'the shared screener'}, so Claude would not see{' '}
          {missing.length === 1 ? 'it' : 'them'}. The picks use the screener’s figures, so{' '}
          {candidates.data?.own ? (
            <>
              add {missing.length === 1 ? 'it' : 'them'} to “Also refresh these tickers” in your{' '}
              <Link to="/settings#nightly-refresh">nightly refresh</Link>; the next refresh brings
              them in.
            </>
          ) : (
            <>
              turn on your own <Link to="/settings#nightly-refresh">nightly refresh</Link> and add{' '}
              {missing.length === 1 ? 'it' : 'them'} to “Also refresh these tickers” there.
            </>
          )}
        </Notice>
      )}
      <details
        className="picks-candidates"
        onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}
      >
        <summary>
          See every ticker Claude would weigh
          {candidates.data ? ` (${candidates.data.candidates.length})` : ''}
        </summary>
        <div className="stack" style={{ gap: 8, marginTop: 8 }}>
          <label className="field" style={{ maxWidth: 360 }}>
            <span>Search the list</span>
            <input
              type="search"
              placeholder="Ticker, name, or sector"
              value={text}
              onChange={(event) => {
                setText(event.target.value)
                setPage(1)
              }}
            />
          </label>
          {candidates.isLoading && <span className="muted">Loading the candidates…</span>}
          {candidates.error !== null && <ErrorNotice error={candidates.error} />}
          {candidates.data && rows.length === 0 && (
            <span className="muted">
              None match. Add it above to have Claude weigh it with the rest.
            </span>
          )}
          {shown.length > 0 && (
            <ul className="picks-candidate-list">
              {shown.map((row) => (
                <li key={row.symbol}>
                  <Link to={`/stocks/${row.symbol}`} className="picks-candidate-symbol">
                    {row.symbol}
                  </Link>
                  <span className="picks-candidate-name">{row.name}</span>
                  <span className="muted">
                    {row.added && <span className="badge">Added by you</span>}{' '}
                    {row.lenses.length > 0 ? `Leads on ${listed(row.lenses)}` : ''}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {rows.length > 0 && (
            <Pager
              page={current}
              pageSize={pageSize}
              total={rows.length}
              onPage={setPage}
              onPageSize={(size) => {
                setPageSize(size)
                setPage(1)
                writeStored(PAGE_SIZE_KEY, String(size))
              }}
              noun="tickers"
            />
          )}
        </div>
      </details>
    </div>
  )
}
