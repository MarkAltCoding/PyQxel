import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  disableNightlyRefresh,
  enableNightlyRefresh,
  getNightlyRefresh,
  previewNightlyRefresh,
} from '../api/endpoints'
import type {
  Account,
  NightlyKeyUse,
  NightlyRefreshStatus,
  ProviderUsage,
  UsageResponse,
} from '../api/types'
import { dateTime, percent, shortDate } from '../lib/format'
import { DATA_PROVIDER_NAMES, hasKey } from '../lib/keyCoverage'
import {
  ALL_STOCKS,
  DEFAULT_STOCKS,
  DEFAULT_TIME_ZONE,
  inTimeZone,
  NIGHTLY_QUERY_KEY,
  otherTimeZones,
  PRICE_HISTORY_CHOICES,
  PRICE_KEYS,
  timeZoneLabel,
  US_TIME_ZONES,
} from '../lib/nightlyRefresh'
import { requestLimit } from '../lib/usage'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'
import { PresetOrCustom } from './PresetOrCustom'
import { Stat } from './Stat'

type PriceKey = (typeof PRICE_KEYS)[number]

/** Opting in to, and the status of, the nightly refresh of the user's own screener. */
export function NightlyRefreshCard({
  account,
  usage,
}: {
  account: Account
  /** The keys' plan usage, to weigh what a refresh uses against. */
  usage?: UsageResponse
}) {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: NIGHTLY_QUERY_KEY, queryFn: getNightlyRefresh })
  const [time, setTime] = useState<string | null>(null)
  const [zone, setZone] = useState<string | null>(null)
  const [keys, setKeys] = useState<PriceKey[] | null>(null)
  const [stocks, setStocks] = useState<number | null>(null)
  const onChanged = (data: NightlyRefreshStatus) => {
    queryClient.setQueryData(NIGHTLY_QUERY_KEY, data)
    setTime(null)
    setZone(null)
    setKeys(null)
    setStocks(null)
  }
  const enable = useMutation({
    mutationFn: enableNightlyRefresh,
    onSuccess: onChanged,
  })
  const disable = useMutation({ mutationFn: disableNightlyRefresh, onSuccess: onChanged })
  // Each attempt clears what the other left, so turning it off never shows why turning
  // it on failed earlier.
  const turn = (on: boolean) => {
    enable.reset()
    disable.reset()
    if (on) enable.mutate(choice)
    else disable.mutate()
  }
  const data = status.data
  const busy = enable.isPending || disable.isPending
  const hasFinnhub = Boolean(account.finnhub?.configured)
  const shownTime = time ?? data?.refresh_time ?? '19:00'
  const shownZone = zone ?? data?.time_zone ?? DEFAULT_TIME_ZONE
  // Until the user chooses, a new refresh uses Tiingo alone for prices, the free plan that
  // serves the most, and a refresh already on uses what it stored.
  const available = PRICE_KEYS.filter((key) => hasKey(account, key))
  const savedKeys = data?.enabled
    ? available.filter((key) => (data.providers ?? []).includes(key))
    : available.filter((key) => key === 'tiingo')
  const shownKeys = keys ?? savedKeys
  // A refresh stored before the cap existed has none (null): it priced all it could.
  const savedStocks = !data?.enabled
    ? DEFAULT_STOCKS
    : data.price_history_stocks === null
      ? ALL_STOCKS
      : (data.price_history_stocks ?? DEFAULT_STOCKS)
  const shownStocks = stocks ?? savedStocks
  const changed =
    (time !== null && time !== data?.refresh_time) ||
    (zone !== null && zone !== data?.time_zone) ||
    (keys !== null && keys.join() !== savedKeys.join()) ||
    (stocks !== null && stocks !== savedStocks)
  const choice = {
    refresh_time: shownTime,
    time_zone: shownZone,
    use_keys: ['finnhub' as const, ...shownKeys],
    price_history_stocks: shownStocks,
  }
  const preview = useQuery({
    queryKey: ['nightly-preview', choice.use_keys.join(), shownStocks],
    queryFn: () => previewNightlyRefresh(choice.use_keys, shownStocks),
    enabled: hasFinnhub && Boolean(data?.available),
    placeholderData: keepPreviousData,
  })
  const error = enable.error ?? disable.error ?? status.error
  /** Off, and this browser has no Finnhub key to turn it on with. */
  const needsFinnhub = Boolean(data?.available && !data.enabled && !hasFinnhub)

  return (
    <section id="nightly-refresh" className="card stack" style={{ gap: 14 }}>
      <div>
        <div className="heading-with-info">
          <h2>Nightly screener refresh</h2>
          <InfoTip topic="nightlyRefresh" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Builds your own screener of US stocks and ETFs every night from your keys: the SEC’s list
          of stocks and their financials, Finnhub’s list of ETFs and its free metrics on your
          Finnhub key, and price-history columns (momentum, volatility, drawdown, factor betas) from
          the price-history keys you choose below, for as many stocks and ETFs as you choose. A
          refresh takes about two hours at Finnhub’s free pace; once a week it checks every US ETF
          for new and newly traded ones, which takes about four. It runs while your browser is
          closed, so turning it on stores your market data keys on PyQxel’s server, encrypted. Your
          Anthropic key is never stored. Turning it off, or deleting your account, deletes them at
          once.
        </p>
      </div>

      {data && !data.available && (
        <Notice tone="warning">
          This server cannot store keys yet, so the nightly refresh is not available. Its owner
          needs to set CREDENTIALS_ENCRYPTION_KEY.
        </Notice>
      )}
      <div className="filter-row" style={{ alignItems: 'flex-end' }}>
        <label className="filter-row" style={{ alignItems: 'center', gap: 10 }}>
          <input
            type="checkbox"
            // Checked only once the server says it is on: never before a Finnhub key is
            // added. Turning it off is never blocked, with or without the key.
            checked={Boolean(data?.enabled)}
            disabled={!data?.available || busy || (!data.enabled && !hasFinnhub)}
            aria-describedby={needsFinnhub ? 'nightly-needs-finnhub' : undefined}
            onChange={(event) => turn(event.target.checked)}
          />
          <span>Refresh my own screener every night</span>
        </label>
        {needsFinnhub && (
          // On a line of its own, right under the box it explains.
          <p
            id="nightly-needs-finnhub"
            className="disclaimer"
            style={{ margin: 0, flexBasis: '100%' }}
          >
            To turn this on, add a Finnhub key under Provider keys above. Finnhub’s free plan is
            enough: it lists the stocks and ETFs and supplies their metrics.
          </p>
        )}
        <label className="field" style={{ width: 150 }}>
          <span>Start at</span>
          <input
            type="time"
            value={shownTime}
            disabled={!data?.available || busy || !hasFinnhub}
            onChange={(event) => setTime(event.target.value)}
          />
        </label>
        <label className="field" style={{ width: 300, maxWidth: '100%' }}>
          <span>Time zone</span>
          <select
            value={shownZone}
            disabled={!data?.available || busy || !hasFinnhub}
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
        {data?.enabled && changed && hasFinnhub && (
          <button
            type="button"
            className="button primary"
            disabled={busy || !shownTime}
            onClick={() => enable.mutate(choice)}
          >
            Save changes
          </button>
        )}
      </div>

      {data?.enabled && !hasFinnhub && (
        <Notice>
          It is on, with the keys stored on the server. You can turn it off here at any time. To
          change its keys, time or number of stocks, add your Finnhub key above first.
        </Notice>
      )}

      {data?.available && hasFinnhub && (
        <div className="stack" style={{ gap: 10 }}>
          <div className="filter-row" style={{ alignItems: 'center' }}>
            <span>Price-history keys it may use:</span>
            {available.length === 0 && <span className="muted">none in this browser</span>}
            {available.map((key) => (
              <label key={key} className="filter-row" style={{ alignItems: 'center', gap: 6 }}>
                <input
                  type="checkbox"
                  checked={shownKeys.includes(key)}
                  disabled={busy}
                  onChange={(event) =>
                    setKeys(
                      PRICE_KEYS.filter((k) =>
                        k === key ? event.target.checked : shownKeys.includes(k),
                      ),
                    )
                  }
                />
                <span>{DATA_PROVIDER_NAMES[key]}</span>
              </label>
            ))}
          </div>
          <PresetOrCustom
            label="Stocks given price histories each night"
            presets={PRICE_HISTORY_CHOICES}
            value={shownStocks}
            onChange={setStocks}
            min={0}
            max={ALL_STOCKS}
            unit="stocks, largest first"
          />
          <KeyUseTable
            uses={preview.data?.key_use ?? []}
            reports={usage?.providers ?? []}
            loading={preview.isLoading}
          />
        </div>
      )}

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

/** What one refresh sends on each key, and its share of each plan's daily or monthly limit. */
function KeyUseTable({
  uses,
  reports,
  loading,
}: {
  uses: NightlyKeyUse[]
  reports: ProviderUsage[]
  loading: boolean
}) {
  if (loading) return <p className="muted">Working out what a refresh would use…</p>
  if (uses.length === 0) return null
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="heading-with-info">
        <h3>What one refresh uses</h3>
        <InfoTip topic="nightlyKeyUse" />
      </div>
      <ul className="stack" style={{ gap: 4, margin: 0, paddingLeft: 18 }}>
        {uses.map((use) => (
          <li key={use.provider}>
            <strong>{DATA_PROVIDER_NAMES[use.provider]}:</strong> about{' '}
            {use.requests.toLocaleString('en-US')} requests.{' '}
            {shareOf(
              use,
              reports.find((report) => report.provider === use.provider),
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

/** How a refresh's requests on one key compare with that key's plan, in words. */
function shareOf(use: NightlyKeyUse, report: ProviderUsage | undefined): string {
  if (use.provider === 'finnhub') {
    const minutes = Math.ceil(use.requests / 55)
    return `No daily limit; paced under its 60 a minute, which takes about ${minutes.toLocaleString('en-US')} minutes.`
  }
  const day = requestLimit(report, 'day')
  const hour = requestLimit(report, 'hour')
  const parts: string[] = []
  if (day?.limit) {
    const share = use.requests / day.limit
    parts.push(
      `${percent(Math.min(share, 9.99), 0)} of its ${day.limit.toLocaleString('en-US')} a day${share >= 1 ? ': it would use up the day, and stop there' : ''}`,
    )
  }
  if (hour?.limit && use.requests > 0) {
    parts.push(
      `${percent(Math.min(use.requests / hour.limit, 9.99), 0)} of its ${hour.limit.toLocaleString('en-US')} an hour`,
    )
  }
  const symbols = report?.limits?.find((limit) => limit.unit === 'symbols')
  if (symbols?.limit && use.requests > 0) {
    parts.push(
      `up to ${percent(Math.min(use.requests / symbols.limit, 1), 0)} of its ${symbols.limit.toLocaleString('en-US')} different symbols a month, fewer as the same stocks repeat each night`,
    )
  }
  return parts.length > 0 ? `That is ${parts.join(', ')}.` : ''
}
