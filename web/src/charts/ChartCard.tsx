import { useState, type ReactNode } from 'react'
import { InfoTip } from '../components/InfoTip'
import { ErrorNotice } from '../components/Notice'
import type { InfoKey } from '../lib/glossary'

interface Props {
  /** The card's id, so a link ending in #id can point at it. */
  id?: string
  title: string
  /** The glossary entry explaining what the chart shows: every chart needs one. */
  info: InfoKey
  subtitle?: ReactNode
  actions?: ReactNode
  /** The chart; omitted while there is nothing to draw yet. */
  chart?: ReactNode
  /** The same values as a table, for reading without hovering or seeing color. */
  table?: ReactNode
  legend?: ReactNode
  loading?: boolean
  /** True while new data loads over a previous render, which stays visible but dimmed. */
  refreshing?: boolean
  error?: unknown
  empty?: ReactNode
  footer?: ReactNode
}

export function ChartCard(props: Props) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  const hasContent = Boolean(props.chart)
  return (
    <section id={props.id} className="card" aria-busy={props.loading || props.refreshing}>
      <div className="card-header">
        <div>
          <div className="heading-with-info">
            <h2>{props.title}</h2>
            <InfoTip topic={props.info} />
          </div>
          {props.subtitle && <div className="subtitle">{props.subtitle}</div>}
        </div>
        <div className="filter-row">
          {props.actions}
          {hasContent && props.table && (
            <div className="segmented" role="group" aria-label={`${props.title} view`}>
              <button
                type="button"
                aria-pressed={view === 'chart'}
                onClick={() => setView('chart')}
              >
                Chart
              </button>
              <button
                type="button"
                aria-pressed={view === 'table'}
                onClick={() => setView('table')}
              >
                Table
              </button>
            </div>
          )}
        </div>
      </div>
      {props.error !== undefined && props.error !== null && <ErrorNotice error={props.error} />}
      {hasContent ? (
        <div className={`card-body${props.refreshing ? ' refreshing' : ''}`}>
          {view === 'chart' ? (
            <>
              {props.legend}
              {props.chart}
            </>
          ) : (
            <div className="table-wrap">{props.table}</div>
          )}
        </div>
      ) : props.loading ? (
        <div className="empty">
          <span className="visually-hidden">Loading</span>
          <div className="spinner" style={{ margin: '0 auto' }} />
        </div>
      ) : (
        !props.error && props.empty && <div className="empty">{props.empty}</div>
      )}
      {props.footer}
    </section>
  )
}

interface LegendItem {
  label: string
  color: string
  shape?: 'line' | 'box' | 'dashed'
}

/** A legend whose keys mirror the marks: a line for lines, a box for areas and bars. */
export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <div className="chart-legend">
      {items.map((item) => (
        <span key={item.label}>
          <i
            className={item.shape === 'box' ? 'box' : item.shape === 'dashed' ? 'dashed' : ''}
            style={{ background: item.color, color: item.color }}
          />
          {item.label}
        </span>
      ))}
    </div>
  )
}
