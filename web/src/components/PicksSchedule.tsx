import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  checkScheduleConflicts,
  deletePicksSchedule,
  getPicksSchedule,
  savePicksSchedule,
} from '../api/endpoints'
import type { PicksRequest, PicksScheduleSettings, PicksScheduleStatus } from '../api/types'
import { dateTime } from '../lib/format'
import { providerKeys } from '../lib/providerKeys'
import { MONTH_DAYS, WEEKDAYS } from '../lib/schedule'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'

const SCHEDULE_KEY = ['picks-schedule'] as const

const browserZone = (): string => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'America/New_York'
  } catch {
    return 'America/New_York'
  }
}

interface Draft {
  weeklyOn: boolean
  weekday: number
  weeklyTime: string
  monthlyOn: boolean
  monthDay: number
  monthlyTime: string
}

function draftOf(status: PicksScheduleStatus | undefined): Draft {
  return {
    weeklyOn: Boolean(status?.weekly),
    weekday: status?.weekly?.weekday ?? 6,
    weeklyTime: status?.weekly?.time ?? '09:00',
    monthlyOn: Boolean(status?.monthly),
    monthDay: status?.monthly?.month_day ?? -1,
    monthlyTime: status?.monthly?.time ?? '09:00',
  }
}

/**
 * Claude's Picks made by themselves, weekly (to hold a week; Sunday by default), monthly
 * (to hold a month; the last day by default), or both, with the settings of the form
 * above. Saving checks the times against the screener refresh and says where they would
 * meet, letting the person keep them anyway.
 */
export function PicksSchedule({ request }: { request: () => PicksRequest }) {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: SCHEDULE_KEY, queryFn: getPicksSchedule })
  const [draft, setDraft] = useState<Draft | null>(null)
  const [conflicts, setConflicts] = useState<string[]>([])
  const shown = draft ?? draftOf(status.data)
  const edit = (next: Partial<Draft>) => {
    setConflicts([])
    setDraft({ ...shown, ...next })
  }
  const done = (data: PicksScheduleStatus) => {
    queryClient.setQueryData(SCHEDULE_KEY, data)
    setDraft(null)
    setConflicts([])
  }
  const save = useMutation({ mutationFn: savePicksSchedule, onSuccess: done })
  const stop = useMutation({ mutationFn: deletePicksSchedule, onSuccess: done })
  const settings = (): PicksScheduleSettings => ({
    weekly: shown.weeklyOn ? { weekday: shown.weekday, time: shown.weeklyTime } : null,
    monthly: shown.monthlyOn ? { month_day: shown.monthDay, time: shown.monthlyTime } : null,
    time_zone: status.data?.time_zone ?? browserZone(),
    request: request(),
  })
  const saveChecked = async () => {
    const chosen = settings()
    try {
      const found = await checkScheduleConflicts({ picks: chosen })
      if (found.warnings.length > 0) {
        setConflicts(found.warnings)
        return
      }
    } catch {
      // The check is a courtesy: if it cannot run, saving goes ahead.
    }
    save.mutate(chosen)
  }
  const data = status.data
  const hasKey = Boolean(providerKeys().anthropic)
  const nothing = !shown.weeklyOn && !shown.monthlyOn
  const busy = save.isPending || stop.isPending
  const zone = data?.time_zone ?? browserZone()

  return (
    <section id="picks-schedule" className="card stack" style={{ gap: 12 }}>
      <div>
        <div className="heading-with-info">
          <h2>Make picks on a schedule</h2>
          <InfoTip topic="picksSchedule" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Claude makes new picks by itself, with what you set above, and adds each list to your
          saved picks. It runs while your browser is closed, so saving stores your Anthropic key and
          market data keys on PyQxel’s server, encrypted; turning it off deletes them. Times are in{' '}
          {zone}.
        </p>
      </div>
      {data && !data.available && (
        <Notice tone="warning">
          This server cannot store keys yet, so scheduled picks are not available.
        </Notice>
      )}
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <label className="checkbox" style={{ minWidth: 230 }}>
          <input
            type="checkbox"
            checked={shown.weeklyOn}
            disabled={busy}
            onChange={(event) => edit({ weeklyOn: event.target.checked })}
          />
          Every week, to hold for a week
        </label>
        <label className="field" style={{ width: 150 }}>
          <span>On</span>
          <select
            value={shown.weekday}
            disabled={busy || !shown.weeklyOn}
            onChange={(event) => edit({ weekday: Number(event.target.value) })}
          >
            {WEEKDAYS.map((name, index) => (
              <option key={name} value={index}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ width: 130 }}>
          <span>At</span>
          <input
            type="time"
            aria-label="Weekly picks time"
            value={shown.weeklyTime}
            disabled={busy || !shown.weeklyOn}
            onChange={(event) => edit({ weeklyTime: event.target.value || '09:00' })}
          />
        </label>
      </div>
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <label className="checkbox" style={{ minWidth: 230 }}>
          <input
            type="checkbox"
            checked={shown.monthlyOn}
            disabled={busy}
            onChange={(event) => edit({ monthlyOn: event.target.checked })}
          />
          Every month, to hold for a month
        </label>
        <label className="field" style={{ width: 240, maxWidth: '100%' }}>
          <span>On</span>
          <select
            value={shown.monthDay}
            disabled={busy || !shown.monthlyOn}
            onChange={(event) => edit({ monthDay: Number(event.target.value) })}
          >
            {MONTH_DAYS.map((day) => (
              <option key={day.value} value={day.value}>
                {day.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field" style={{ width: 130 }}>
          <span>At</span>
          <input
            type="time"
            aria-label="Monthly picks time"
            value={shown.monthlyTime}
            disabled={busy || !shown.monthlyOn}
            onChange={(event) => edit({ monthlyTime: event.target.value || '09:00' })}
          />
        </label>
      </div>
      {shown.weeklyOn && shown.monthlyOn && shown.weeklyTime === shown.monthlyTime && (
        <p className="disclaimer" style={{ margin: 0 }}>
          On a day both fall on, they run together in one go.
        </p>
      )}
      {conflicts.length > 0 && (
        <Notice tone="warning">
          <div className="stack" style={{ gap: 6 }}>
            <span>These times run into your screener refresh:</span>
            <ul style={{ margin: 0 }}>
              {conflicts.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
            <div className="filter-row">
              <button
                type="button"
                className="button small"
                onClick={() => {
                  setConflicts([])
                  save.mutate(settings())
                }}
              >
                Save anyway
              </button>
              <button type="button" className="button ghost small" onClick={() => setConflicts([])}>
                Change them
              </button>
            </div>
          </div>
        </Notice>
      )}
      <div className="filter-row" style={{ alignItems: 'center' }}>
        <button
          type="button"
          className="button primary"
          disabled={busy || nothing || !hasKey || data?.available === false}
          onClick={() => void saveChecked()}
        >
          {data?.enabled ? 'Save the schedule' : 'Turn on scheduled picks'}
        </button>
        {data?.enabled && (
          <button
            type="button"
            className="button ghost danger"
            disabled={busy}
            onClick={() => stop.mutate()}
          >
            Turn off and delete the stored keys
          </button>
        )}
        {!hasKey && <span className="disclaimer">Add an Anthropic key in Settings first.</span>}
      </div>
      {data?.enabled && (
        <p className="disclaimer" style={{ margin: 0 }}>
          {data.next_weekly_at && `Next weekly picks: ${dateTime(data.next_weekly_at)}. `}
          {data.next_monthly_at && `Next monthly picks: ${dateTime(data.next_monthly_at)}. `}
          They start on the hourly check after their time. Saving again sends the form’s current
          settings and your keys as they are now.
        </p>
      )}
      {data?.last_error && (
        <Notice tone="warning">The last scheduled run: {data.last_error}</Notice>
      )}
      {(save.error ?? stop.error ?? status.error) !== null && (
        <ErrorNotice error={save.error ?? stop.error ?? status.error} />
      )}
    </section>
  )
}
