import type { ReactNode } from 'react'
import type { InfoKey } from '../lib/glossary'
import { InfoTip } from './InfoTip'

/** A labelled figure, with an "i" explaining it: every stat needs an explanation. */
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
  return (
    <div className="stat">
      <div className="stat-label">
        {label}
        <InfoTip topic={info} />
      </div>
      <div className={`stat-value${className ? ` ${className}` : ''}`}>{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}
