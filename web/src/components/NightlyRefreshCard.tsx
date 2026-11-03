import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { disableNightlyRefresh, enableNightlyRefresh, getNightlyRefresh } from '../api/endpoints'
import type { Account, NightlyRefreshStatus } from '../api/types'
import { dateTime, shortDate } from '../lib/format'
import { DATA_PROVIDER_NAMES } from '../lib/keyCoverage'
import {
  DEFAULT_TIME_ZONE,
  inTimeZone,
  NIGHTLY_QUERY_KEY,
  otherTimeZones,
  timeZoneLabel,
  US_TIME_ZONES,
} from '../lib/nightlyRefresh'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'
import { Stat } from './Stat'

/** Opting in to, and the status of, the nightly refresh of the user's own screener. */
export function NightlyRefreshCard({ account }: { account: Account }) {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: NIGHTLY_QUERY_KEY, queryFn: getNightlyRefresh })
  const [time, setTime] = useState<string | null>(null)
  const [zone, setZone] = useState<string | null>(null)
  const onChanged = (data: NightlyRefreshStatus) => {
    queryClient.setQueryData(NIGHTLY_QUERY_KEY, data)
    setTime(null)
    setZone(null)
  }
  const enable = useMutation({
    mutationFn: ({ refresh_time, time_zone }: { refresh_time: string; time_zone: string }) =>
      enableNightlyRefresh({ refresh_time, time_zone }),
    onSuccess: onChanged,
  })
  const disable = useMutation({ mutationFn: disableNightlyRefresh, onSuccess: onChanged })
  const data = status.data
  const busy = enable.isPending || disable.isPending
  const hasFinnhub = Boolean(account.finnhub?.configured)
  const shownTime = time ?? data?.refresh_time ?? '19:00'
  const shownZone = zone ?? data?.time_zone ?? DEFAULT_TIME_ZONE
  const changed =
    (time !== null && time !== data?.refresh_time) || (zone !== null && zone !== data?.time_zone)
  const choice = { refresh_time: shownTime, time_zone: shownZone }
  const error = enable.error ?? disable.error ?? status.error

  return (
    <section id="nightly-refresh" className="card stack" style={{ gap: 14 }}>
      <div>
        <div className="heading-with-info">
          <h2>Nightly screener refresh</h2>
          <InfoTip topic="nightlyRefresh" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Builds your own stock screener every night from your keys: the SEC’s list of stocks and
          their financials, Finnhub’s free metrics on your Finnhub key, and price-history columns
          (momentum, volatility, drawdown, factor betas) from whichever price-history key you have.
          It runs while your browser is closed, so turning it on stores your market data keys on
          PyQxel’s server, encrypted. Your Anthropic key is never stored. Turning it off, or
          deleting your account, deletes them at once.
        </p>
      </div>

      {data && !data.available && (
        <Notice tone="warning">
          This server cannot store keys yet, so the nightly refresh is not available. Its owner
          needs to set CREDENTIALS_ENCRYPTION_KEY.
        </Notice>
      )}
      {data?.available && !hasFinnhub && !data.enabled && (
        <Notice>Add your Finnhub key above first: its free plan fills the screener.</Notice>
      )}

      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <label className="filter-row" style={{ alignItems: 'center', gap: 10 }}>
          <input
            type="checkbox"
            checked={Boolean(data?.enabled)}
            disabled={!data?.available || busy || (!data.enabled && !hasFinnhub)}
            onChange={(event) => (event.target.checked ? enable.mutate(choice) : disable.mutate())}
          />
          <span>Refresh my own screener every night</span>
        </label>
        <label className="field" style={{ width: 150 }}>
          <span>Start at</span>
          <input
            type="time"
            value={shownTime}
            disabled={!data?.available || busy}
            onChange={(event) => setTime(event.target.value)}
          />
        </label>
        <label className="field" style={{ width: 300, maxWidth: '100%' }}>
          <span>Time zone</span>
          <select
            value={shownZone}
            disabled={!data?.available || busy}
            onChange={(event) => setZone(event.target.value)}
          >
            <optgroup label="United States">
              {US_TIME_ZONES.map((us) => (
                <option key={us.value} value={us.value}>
                  {us.label}
                </option>
              ))}
            </optgroup>
            <optgroup label="Elsewhere">
              {otherTimeZones().map((other) => (
                <option key={other} value={other}>
                  {timeZoneLabel(other)}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
        {data?.enabled && changed && (
          <button
            type="button"
            className="button primary"
            disabled={busy || !shownTime}
            onClick={() => enable.mutate(choice)}
          >
            Save time
          </button>
        )}
      </div>

      {data && <RefreshStatus status={data} />}
      {error !== null && <ErrorNotice error={error} />}
    </section>
  )
}

function RefreshStatus({ status }: { status: NightlyRefreshStatus }) {
  const latest = status.latest_run
  const success = status.last_success
  return (
    <>
      {status.enabled && (
        <p className="disclaimer">
          Stored, encrypted:{' '}
          {(status.providers ?? []).map((p) => DATA_PROVIDER_NAMES[p]).join(', ')}
          {status.keys_updated_at && ` (since ${dateTime(status.keys_updated_at)})`}. Changing a key
          above updates the stored copy.
        </p>
      )}
      {latest?.status === 'running' && (
        <Notice>
          Refreshing now, since {dateTime(latest.started_at)}. It takes an hour or two.
        </Notice>
      )}
      {latest?.status === 'failed' && (
        <Notice tone="warning">
          The latest refresh, {dateTime(latest.started_at)}, failed: {latest.error}
        </Notice>
      )}
      {(status.enabled || success) && (
        <div className="stats">
          <Stat
            info="nightlyLastRefresh"
            label="Last refresh"
            value={success?.finished_at ? dateTime(success.finished_at) : 'Not yet'}
            detail={
              success?.prices_as_of ? `Prices as of ${shortDate(success.prices_as_of)}` : undefined
            }
          />
          <Stat
            info="nightlyStocks"
            label="Stocks in your screener"
            value={success?.stocks?.toLocaleString() ?? '–'}
          />
          <Stat
            info="nightlyPriced"
            label="With price history"
            value={success?.priced?.toLocaleString() ?? '–'}
          />
          {status.enabled && (
            <Stat
              info="nightlyNextRefresh"
              label="Next refresh"
              value={
                status.next_run_at
                  ? inTimeZone(status.next_run_at, status.time_zone ?? DEFAULT_TIME_ZONE)
                  : '–'
              }
              detail="Starts within the hour after"
            />
          )}
        </div>
      )}
    </>
  )
}
