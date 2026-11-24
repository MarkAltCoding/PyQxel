import type { ReactNode } from 'react'
import type { InfoKey } from '../lib/glossary'
import { InfoTip } from './InfoTip'

/** A labeled figure, with an "i" explaining it: every stat needs an explanation. */
export function Stat({
  label,
  value,
  detail,
  className,
  info,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  className?: string
  /** The glossary entry explaining this stat. */
  info: InfoKey
}) {
  // The last word stays with the "i", so a label that wraps never leaves the "i" alone
  // on a line of its own.
  const split = label.lastIndexOf(' ')
  return (
    <div className="stat">
      <div className="stat-label">
        {split > 0 && label.slice(0, split + 1)}
        <span className="nowrap">
          {label.slice(split + 1)}
          <InfoTip topic={info} />
        </span>
      </div>
      <div className={`stat-value${className ? ` ${className}` : ''}`}>{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}
