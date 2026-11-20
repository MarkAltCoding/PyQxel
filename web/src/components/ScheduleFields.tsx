import { useState } from 'react'
import type { RefreshSchedule } from '../api/types'
import {
  FREQUENCIES,
  MONTH_DAYS,
  WEEKDAYS,
  isoDay,
  pickerMonths,
  type Frequency,
} from '../lib/schedule'
import { InfoTip } from './InfoTip'

/**
 * When a refresh runs, at most once a day: every night, each day the NYSE is open,
 * weeknights, one day a week or a month, or dates chosen on a calendar of this month and
 * the next; and, if wanted, a time of its own for some days of the week.
 */
export function ScheduleFields({
  schedule,
  onChange,
  baseTime,
  disabled = false,
}: {
  schedule: RefreshSchedule
  onChange: (next: RefreshSchedule) => void
  /** The usual time, which days without their own use. */
  baseTime: string
  disabled?: boolean
}) {
  const [picking, setPicking] = useState(false)
  const frequency = schedule.frequency ?? 'nightly'
  const times = schedule.times ?? {}
  const dates = schedule.dates ?? []
  const set = (next: Partial<RefreshSchedule>) => onChange({ ...schedule, ...next })
  const setTime = (day: number, value: string | null) => {
    const next = { ...times }
    if (value === null) delete next[day]
    else next[day] = value
    set({ times: next })
  }
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <span className="heading-with-info" style={{ alignItems: 'flex-end' }}>
          <label className="field" style={{ width: 260, maxWidth: '100%' }}>
            <span>How often</span>
            <select
              value={frequency}
              disabled={disabled}
              onChange={(event) => set({ frequency: event.target.value as Frequency })}
            >
              {FREQUENCIES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <InfoTip topic="refreshSchedule" />
        </span>
        {frequency === 'weekly' && (
          <label className="field" style={{ width: 160 }}>
            <span>On</span>
            <select
              value={schedule.weekday ?? 6}
              disabled={disabled}
              onChange={(event) => set({ weekday: Number(event.target.value) })}
            >
              {WEEKDAYS.map((name, index) => (
                <option key={name} value={index}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
        {frequency === 'monthly' && (
          <label className="field" style={{ width: 240, maxWidth: '100%' }}>
            <span>On</span>
            <select
              value={schedule.month_day ?? -1}
              disabled={disabled}
              onChange={(event) => set({ month_day: Number(event.target.value) })}
            >
              {MONTH_DAYS.map((day) => (
                <option key={day.value} value={day.value}>
                  {day.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {frequency === 'dates' && (
          <button
            type="button"
            className="button"
            disabled={disabled}
            onClick={() => setPicking(true)}
          >
            {dates.length ? `${dates.length} days chosen: change` : 'Choose dates'}
          </button>
        )}
      </div>
      {picking && (
        <DatePicker
          chosen={dates}
          onDone={(next) => {
            set({ dates: next })
            setPicking(false)
          }}
          onCancel={() => setPicking(false)}
        />
      )}
      <details className="schedule-times">
        <summary>
          A different time on some days
          {Object.keys(times).length > 0 ? ` (${Object.keys(times).length} set)` : ''}
        </summary>
        <div className="stack" style={{ gap: 6, marginTop: 8 }}>
          {WEEKDAYS.map((name, index) => {
            const own = times[index]
            return (
              <div key={name} className="filter-row" style={{ alignItems: 'center', gap: 10 }}>
                <label className="checkbox" style={{ width: 130 }}>
                  <input
                    type="checkbox"
                    checked={own !== undefined}
                    disabled={disabled}
                    onChange={(event) => setTime(index, event.target.checked ? baseTime : null)}
                  />
                  {name}
                </label>
                {own !== undefined ? (
                  <input
                    type="time"
                    aria-label={`Time on ${name}`}
                    value={own}
                    disabled={disabled}
                    onChange={(event) => setTime(index, event.target.value || baseTime)}
                  />
                ) : (
                  <span className="muted">at {baseTime}</span>
                )}
              </div>
            )
          })}
        </div>
      </details>
    </div>
  )
}

/** Days of the rest of this month and all of the next, any number of them chosen. */
function DatePicker({
  chosen,
  onDone,
  onCancel,
}: {
  chosen: string[]
  onDone: (dates: string[]) => void
  onCancel: () => void
}) {
  // Read once, when the calendar opens, so a render never moves the days.
  const [today] = useState(() => new Date())
  const todayIso = isoDay(today)
  const [picked, setPicked] = useState(() => new Set(chosen.filter((d) => d >= todayIso)))
  const toggle = (day: string) => {
    const next = new Set(picked)
    if (next.has(day)) next.delete(day)
    else next.add(day)
    setPicked(next)
  }
  return (
    <div className="dialog-backdrop" role="presentation" onClick={onCancel}>
      <div
        className="dialog card stack date-picker"
        role="dialog"
        aria-modal="true"
        aria-label="Choose the days to refresh"
        onClick={(event) => event.stopPropagation()}
      >
        <h3>Choose the days to refresh</h3>
        <p className="disclaimer">
          Any days from today through the end of next month; it runs at most once a day.
        </p>
        <div className="date-picker-months">
          {pickerMonths(today).map((month) => (
            <div key={month.title}>
              <div className="date-picker-title">{month.title}</div>
              <div className="date-picker-grid" role="group" aria-label={month.title}>
                {WEEKDAYS.map((name) => (
                  <span key={name} className="date-picker-weekday" aria-hidden="true">
                    {name.slice(0, 2)}
                  </span>
                ))}
                {month.days.map((day, index) => {
                  if (day === null) return <span key={`blank-${index}`} />
                  const iso = isoDay(day)
                  const past = iso < todayIso
                  return (
                    <button
                      key={iso}
                      type="button"
                      className="date-picker-day"
                      aria-pressed={picked.has(iso)}
                      aria-label={day.toLocaleDateString('en-US', {
                        weekday: 'long',
                        month: 'long',
                        day: 'numeric',
                      })}
                      disabled={past}
                      onClick={() => toggle(iso)}
                    >
                      {day.getDate()}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
        <div className="filter-row" style={{ justifyContent: 'flex-end' }}>
          <button type="button" className="button ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="button primary"
            onClick={() => onDone([...picked].sort())}
          >
            Use {picked.size} {picked.size === 1 ? 'day' : 'days'}
          </button>
        </div>
      </div>
    </div>
  )
}
