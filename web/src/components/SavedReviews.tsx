import { useQuery } from '@tanstack/react-query'
import { listPortfolioReviews } from '../api/endpoints'
import type { PortfolioReviewSummary } from '../api/types'
import { dateTime, listed } from '../lib/format'
import { modelName } from '../lib/modelName'
import { SAVED_REVIEWS_KEY } from '../lib/savedReviews'
import { InfoTip } from './InfoTip'
import { ErrorNotice } from './Notice'

function madeFor(item: PortfolioReviewSummary): string {
  const parts = [
    item.risk_tolerance ? `${item.risk_tolerance} risk` : '',
    item.horizon_years ? `${Number(item.horizon_years.toFixed(1))}-year horizon` : '',
    item.notes ? `“${item.notes}”` : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' · ') : 'no goals given'
}

/** Every portfolio review Claude has written for the account, newest first; a row opens
 * it in the review card above. */
export function SavedReviews({
  openedId,
  onOpen,
  onDelete,
  nameOf,
}: {
  /** The review shown above, marked in the table; null for the latest. */
  openedId: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  nameOf: (portfolio: number) => string
}) {
  const saved = useQuery({ queryKey: SAVED_REVIEWS_KEY, queryFn: listPortfolioReviews })
  const items = saved.data?.items ?? []
  return (
    <details className="saved-reviews">
      {/* The "i" is in the body, as in the app's other disclosures: a button inside a
          summary is a control nested in a control, which keyboards and screen readers
          handle badly. */}
      <summary>Saved reviews{items.length > 0 ? ` (${items.length})` : ''}</summary>
      <div className="heading-with-info" style={{ marginTop: 8 }}>
        <span className="disclaimer">Every review Claude writes for you, newest first.</span>
        <InfoTip topic="savedReviews" />
      </div>
      {saved.error !== null && <ErrorNotice error={saved.error} />}
      {saved.data && items.length === 0 && <div className="empty">No reviews yet.</div>}
      {items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>Made</th>
                <th>Portfolios</th>
                <th>
                  <span className="heading-with-info">
                    Made for
                    <InfoTip topic="askedFor" />
                  </span>
                </th>
                <th>Written by</th>
                <th>Verdict</th>
                <th>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  className="clickable"
                  aria-current={item.id === openedId ? 'true' : undefined}
                  onClick={() => onOpen(item.id)}
                >
                  <td>
                    <button
                      type="button"
                      className="link-button"
                      onClick={(event) => {
                        event.stopPropagation()
                        onOpen(item.id)
                      }}
                    >
                      {dateTime(item.generated_at)}
                    </button>
                  </td>
                  <td>
                    {listed(item.portfolios.map(nameOf))}
                    {item.group_mode === 'separate'
                      ? ', each its own plan'
                      : item.group_mode === 'combined'
                        ? ', as one'
                        : ''}
                  </td>
                  <td>{madeFor(item)}</td>
                  <td>{modelName(item.model, item.effort)}</td>
                  <td>{item.headline}</td>
                  <td>
                    <button
                      type="button"
                      className="button ghost small danger"
                      onClick={(event) => {
                        event.stopPropagation()
                        onDelete(item.id)
                      }}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </details>
  )
}
