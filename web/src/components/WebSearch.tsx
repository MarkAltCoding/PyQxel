import type { AiProgress } from '../api/aiStream'
import type { WebResearch } from '../api/types'
import { dateTime } from '../lib/format'
import { safeUrl } from '../lib/safeUrl'
import { InfoTip } from './InfoTip'

/** The site a page is on, as a reader names it: reuters.com. */
function site(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/**
 * The "Let Claude search the web" choice every AI report offers, with what it costs
 * beside it.
 */
export function WebSearchOption({
  checked,
  onChange,
  disabled = false,
}: {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <span className="heading-with-info web-search-option">
      <label className="checkbox">
        <input
          type="checkbox"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span>
          Let Claude search the web <span className="muted">(a few cents more)</span>
        </span>
      </label>
      <InfoTip topic="webSearch" />
    </span>
  )
}

/** What Claude is doing while it writes: the searches it has made, then its reasoning. */
export function AiProgressDetail({ progress }: { progress: AiProgress }) {
  return (
    <>
      {progress.searches.length > 0 && (
        <ul className="web-searches" aria-label="Web searches so far">
          {progress.searches.map((query, index) => (
            <li key={`${index}-${query}`}>Searched the web for “{query}”</li>
          ))}
        </ul>
      )}
      {progress.thinking && <div className="thinking">{progress.thinking}</div>}
    </>
  )
}

/**
 * Every fact Claude brought back from the web for a report, numbered as the report cites
 * it ([3]), each with the pages it came from. Shows nothing when the web was not searched.
 */
export function WebSources({ research }: { research: WebResearch | null | undefined }) {
  if (!research) return null
  const findings = research.findings ?? []
  return (
    <section className="web-sources" aria-label="Sources from the web">
      <div className="heading-with-info">
        <h3>
          From the web <span className="report-tab-count">{findings.length}</span>
        </h3>
        <InfoTip topic="webSources" />
      </div>
      <p className="disclaimer">
        Searched {dateTime(research.searched_at)} · {research.searches}{' '}
        {research.searches === 1 ? 'search' : 'searches'}
        {findings.length > 0 &&
          ' · a number in square brackets in the text, such as [1], points to the fact with that number here'}
      </p>
      {findings.length === 0 ? (
        <p className="muted">
          The search found nothing it could tie to a page, so nothing here comes from the web.
        </p>
      ) : (
        <ol className="web-findings">
          {findings.map((finding) => (
            <li key={finding.id} value={finding.id} id={`web-finding-${finding.id}`}>
              <span>{finding.text}</span>
              <span className="web-finding-sources">
                {finding.sources.map((source) => {
                  const href = safeUrl(source.url)
                  const label = source.title
                    ? `${source.title} (${site(source.url)})`
                    : site(source.url)
                  return href ? (
                    <a
                      key={source.url}
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={source.cited_text ?? undefined}
                    >
                      {label}
                    </a>
                  ) : (
                    <span key={source.url}>{label}</span>
                  )
                })}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
