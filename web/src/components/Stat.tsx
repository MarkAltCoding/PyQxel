import type { ReactNode } from 'react'

export function Stat({
  label,
  value,
  detail,
  className,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  className?: string
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value${className ? ` ${className}` : ''}`}>{value}</div>
      {detail && <div className="stat-detail">{detail}</div>}
    </div>
  )
}
