import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import type { RiskFactor } from '../api/types'
import type { InfoKey } from '../lib/glossary'
import type { ReportView } from '../lib/reportView'
import { badgeTone, SEVERITY_ORDER, SEVERITY_TONE, type Tone } from '../lib/tones'
import { InfoTip } from './InfoTip'
import { Segmented } from './Segmented'

// Building blocks of Claude's reports: a verdict up top, then tabs that each hold one
// short part, so a report reads as a few scannable panels rather than a wall of text.

/** A graded scale, such as low to high risk, with the grade given lit up to it. */
export function LevelMeter({
  label,
  info,
  steps,
  value,
  tone,
}: {
  label: string
  info: InfoKey
  steps: readonly string[]
  value: string
  tone: Tone
}) {
  const index = steps.indexOf(value)
  return (
    <div className="level-meter">
      <div className="heading-with-info level-meter-label">
        <span>{label}</span>
        <InfoTip topic={info} />
      </div>
      <div className={`level-meter-value tone-${tone}`}>{value}</div>
      <div
        className={`level-meter-track tone-${tone}`}
        role="img"
        aria-label={`${label}: ${value}, ${index + 1} of ${steps.length}`}
      >
        {steps.map((step, i) => (
          <span key={step} className={i <= index ? 'lit' : undefined} title={step} />
        ))}
      </div>
    </div>
  )
}

/** Where a stance sits between two ends, as a marker on a track. */
export function StanceMeter({
  value,
  info,
}: {
  value: 'bearish' | 'neutral' | 'bullish'
  info: InfoKey
}) {
  const position = { bearish: 0, neutral: 50, bullish: 100 }[value]
  const tone = { bearish: 'critical', neutral: 'neutral', bullish: 'good' }[value]
  return (
    <div className="level-meter">
      <div className="heading-with-info level-meter-label">
        <span>Stance</span>
        <InfoTip topic={info} />
      </div>
      <div className={`level-meter-value tone-${tone}`}>{value}</div>
      <div className="stance-track" role="img" aria-label={`Stance: ${value}`}>
        <span className={`stance-marker tone-${tone}`} style={{ left: `${position}%` }} />
      </div>
      <div className="stance-ends" aria-hidden="true">
        <span>Bearish</span>
        <span>Bullish</span>
      </div>
    </div>
  )
}

/** The report's opening: its one-line verdict and the grades beside it. */
export function Verdict({ headline, children }: { headline: string; children: ReactNode }) {
  return (
    <div className="report-verdict">
      <p className="report-headline">{headline}</p>
      <div className="report-meters">{children}</div>
    </div>
  )
}

export interface Tab {
  id: string
  label: string
  /** How many items the tab holds, shown beside its label. */
  count?: number
  content: ReactNode
}

/** Tabs for a report's parts, moved between with a click or the arrow keys. */
export function ReportTabs({ label, tabs }: { label: string; tabs: Tab[] }) {
  const base = useId()
  const [selected, setSelected] = useState(tabs[0]?.id)
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const current = tabs.find((tab) => tab.id === selected) ?? tabs[0]
  const move = (event: KeyboardEvent, index: number) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key]
    let next = step === undefined ? undefined : (index + step + tabs.length) % tabs.length
    if (event.key === 'Home') next = 0
    if (event.key === 'End') next = tabs.length - 1
    if (next === undefined) return
    event.preventDefault()
    setSelected(tabs[next]!.id)
    buttons.current[next]?.focus()
  }
  if (!current) return null
  return (
    <div className="report-tabs">
      <div role="tablist" aria-label={label} className="report-tablist">
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            ref={(element) => {
              buttons.current[index] = element
            }}
            type="button"
            role="tab"
            id={`${base}-tab-${tab.id}`}
            aria-selected={tab.id === current.id}
            aria-controls={`${base}-panel-${tab.id}`}
            tabIndex={tab.id === current.id ? 0 : -1}
            onClick={() => setSelected(tab.id)}
            onKeyDown={(event) => move(event, index)}
          >
            {tab.label}
            {tab.count !== undefined && (
              <>
                {' '}
                <span className="report-tab-count">{tab.count}</span>
              </>
            )}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`${base}-panel-${current.id}`}
        aria-labelledby={`${base}-tab-${current.id}`}
        className="report-panel"
        tabIndex={0}
      >
        {current.content}
      </div>
    </div>
  )
}

/** Prose split into paragraphs, showing the first until more is asked for. */
export function Summary({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
  const shown = open ? paragraphs : paragraphs.slice(0, 1)
  return (
    <div className="report-summary">
      {shown.map((paragraph) => (
        <p key={paragraph}>{paragraph}</p>
      ))}
      {paragraphs.length > 1 && (
        <button type="button" className="link-button" onClick={() => setOpen((was) => !was)}>
          {open ? 'Show less' : `Read the full summary (${paragraphs.length} paragraphs)`}
        </button>
      )}
    </div>
  )
}

const MARKS: Record<Tone, string> = { good: '+', critical: '−', warning: '!', neutral: '•' }

/** A titled list of short points, each marked in the column's tone. */
export function PointColumn({
  title,
  items,
  tone,
  empty = 'None given.',
}: {
  title: string
  items: string[]
  tone: Tone
  empty?: string
}) {
  return (
    <div className={`point-column tone-${tone}`}>
      <h3>
        {title} <span className="report-tab-count">{items.length}</span>
      </h3>
      {items.length === 0 ? (
        <p className="muted">{empty}</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item}>
              <span className="point-mark" aria-hidden="true">
                {MARKS[tone]}
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Risks, most severe first, each with a four-step severity bar and its evidence. */
export function RiskCards({ risks }: { risks: RiskFactor[] }) {
  const sorted = [...risks].sort(
    (a, b) => SEVERITY_ORDER.indexOf(b.severity) - SEVERITY_ORDER.indexOf(a.severity),
  )
  if (sorted.length === 0) return <p className="muted">No specific risks named.</p>
  return (
    <ul className="risk-cards">
      {sorted.map((risk) => {
        const level = SEVERITY_ORDER.indexOf(risk.severity)
        const tone = SEVERITY_TONE[risk.severity]
        return (
          <li key={risk.name} className={`risk-card tone-${tone}`}>
            <div className="risk-card-head">
              <strong>{risk.name}</strong>
              <span className={`severity tone-${tone}`}>
                <span
                  className="severity-bars"
                  role="img"
                  aria-label={`Severity ${risk.severity}, ${level + 1} of 4`}
                >
                  {SEVERITY_ORDER.map((step, i) => (
                    <span key={step} className={i <= level ? 'lit' : undefined} />
                  ))}
                </span>
                {risk.severity}
              </span>
            </div>
            <p>{risk.evidence}</p>
          </li>
        )
      })}
    </ul>
  )
}

/** Caveats, kept small and out of the way. */
export function FinePrint({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div className="fine-print">
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

const VIEWS = [
  { value: 'visual', label: 'Visual' },
  { value: 'text', label: 'Full text' },
] as const

/** Switches a report between its panels and one page of full text. */
export function ReportViewToggle({
  view,
  onChange,
}: {
  view: ReportView
  onChange: (view: ReportView) => void
}) {
  return (
    <div className="report-view-toggle">
      <Segmented label="Report view" value={view} options={VIEWS} onChange={onChange} />
    </div>
  )
}

// The full-text view: every part of the report in order, nothing behind a tab or a
// click.

/** All of a summary's paragraphs. */
export function FullText({ text }: { text: string }) {
  return (
    <div className="report-summary">
      {text
        .split(/\n\s*\n/)
        .map((paragraph) => paragraph.trim())
        .filter(Boolean)
        .map((paragraph) => (
          <p key={paragraph}>{paragraph}</p>
        ))}
    </div>
  )
}

/** A titled bulleted list. */
export function TextPoints({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null
  return (
    <div>
      <h3>{title}</h3>
      <ul>
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/** Risks as a bulleted list, each with its severity and evidence. */
export function TextRisks({ risks }: { risks: RiskFactor[] }) {
  return (
    <ul>
      {risks.map((risk) => (
        <li key={risk.name}>
          <strong>{risk.name}</strong>{' '}
          <span className={`badge ${badgeTone(SEVERITY_TONE[risk.severity])}`}>
            {risk.severity}
          </span>
          <div style={{ color: 'var(--text-secondary)' }}>{risk.evidence}</div>
        </li>
      ))}
    </ul>
  )
}
