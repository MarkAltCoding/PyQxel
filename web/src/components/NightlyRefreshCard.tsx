import { useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  disableNightlyRefresh,
  enableNightlyRefresh,
  getBilling,
  getNightlyRefresh,
  previewNightlyRefresh,
} from '../api/endpoints'
import type {
  Account,
  NightlyKeyUse,
  NightlyTimeSlot,
  NightlyRefreshStatus,
  ProviderUsage,
  UsageResponse,
} from '../api/types'
import { BILLING_QUERY_KEY } from '../lib/billing'
import { dateTime, percent, shortDate } from '../lib/format'
import { DATA_PROVIDER_NAMES, hasKey } from '../lib/keyCoverage'
import {
  ALL_THE_PLAN_ALLOWS,
  busiestSlot,
  DEFAULT_TIME_ZONE,
  heldBy,
  inTimeZone,
  limitFromServer,
  limitParams,
  limitToServer,
  NIGHTLY_QUERY_KEY,
  otherTimeZones,
  PRICE_KEYS,
  PRICE_LIMIT_CHOICES,
  slotFor,
  slotSpan,
  timeZoneLabel,
  EVERYTHING,
  MAX_CUSTOM_SYMBOLS,
  UNIVERSE_CHOICES,
  US_TIME_ZONES,
  type NightlyUniverse,
  type PriceKey,
} from '../lib/nightlyRefresh'
import { requestLimit } from '../lib/usage'
import { InfoTip } from './InfoTip'
import { ErrorNotice, Notice } from './Notice'
import { PresetOrCustom } from './PresetOrCustom'
import { SearchBox } from './SearchBox'
import { Stat } from './Stat'

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
  const billing = useQuery({ queryKey: BILLING_QUERY_KEY, queryFn: getBilling })
  const [time, setTime] = useState<string | null>(null)
  const [zone, setZone] = useState<string | null>(null)
  const [keys, setKeys] = useState<PriceKey[] | null>(null)
  const [limits, setLimits] = useState<Partial<Record<PriceKey, number>>>({})
  const [universes, setUniverses] = useState<NightlyUniverse[] | null>(null)
  const [custom, setCustom] = useState<string[] | null>(null)
  const onChanged = (data: NightlyRefreshStatus) => {
    queryClient.setQueryData(NIGHTLY_QUERY_KEY, data)
    setTime(null)
    setZone(null)
    setKeys(null)
    setLimits({})
    setUniverses(null)
    setCustom(null)
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
  // Each price-history key has its own nightly limit: the one stored, or its free plan's
  // allowance until the user chooses.
  const savedLimit = (key: PriceKey) =>
    limitFromServer(data?.enabled ? data.price_history_limits?.[key] : undefined, key)
  const shownLimit = (key: PriceKey) => limits[key] ?? savedLimit(key)
  const serverLimits = Object.fromEntries(
    shownKeys.map((key) => [key, limitToServer(shownLimit(key))]),
  ) as Partial<Record<PriceKey, number | null>>
  const savedUniverses = data?.universes ?? EVERYTHING
  const shownUniverses = universes ?? savedUniverses
  const savedCustom = data?.custom_symbols ?? []
  const shownCustom = custom ?? savedCustom
  /** Nothing chosen: the server refuses a refresh of nothing. */
  const nothing = shownUniverses.length === 0 && shownCustom.length === 0
  const changed =
    (time !== null && time !== data?.refresh_time) ||
    (zone !== null && zone !== data?.time_zone) ||
    (keys !== null && keys.join() !== savedKeys.join()) ||
    shownKeys.some((key) => shownLimit(key) !== savedLimit(key)) ||
    (universes !== null && universes.join() !== savedUniverses.join()) ||
    (custom !== null && custom.join() !== savedCustom.join())
  const choice = {
    refresh_time: shownTime,
    time_zone: shownZone,
    use_keys: ['finnhub' as const, ...shownKeys],
    price_history_limits: serverLimits,
    universes: shownUniverses,
    custom_symbols: shownCustom,
  }
  const preview = useQuery({
    queryKey: [
      'nightly-preview',
      choice.use_keys.join(),
      limitParams(serverLimits).join(),
      shownUniverses.join(),
      shownCustom.join(),
    ],
    queryFn: () =>
      previewNightlyRefresh(
        choice.use_keys,
        limitParams(serverLimits),
        shownUniverses,
        shownCustom,
      ),
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
          refresh of every stock and ETF takes about four hours at Finnhub’s free pace of 60
          requests a minute, about an hour and a half on its paid plan of 150 a minute, and under 20
          minutes on one of 900. Since you pay for the server’s time, a faster plan also makes each
          refresh cheaper. A smaller choice, such as the S&P 500 and a few tickers of your own,
          takes minutes. It runs while your browser is closed, so turning it on stores your market
          data keys on PyQxel’s server, encrypted. Your Anthropic key is never stored. Turning it
          off, or deleting your account, deletes them at once.
        </p>
      </div>

      {data?.enabled && billing.data?.enabled && !billing.data.can_refresh && (
        <Notice tone="warning">
          Your refresh is paused: it runs on PyQxel’s server, which costs money, so it needs credit
          or a subscription. <a href="#billing">Add one under Billing</a>.
        </Notice>
      )}
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
            disabled={busy || !shownTime || nothing}
            onClick={() => enable.mutate(choice)}
          >
            Save changes
          </button>
        )}
      </div>

      {data?.available && hasFinnhub && (data.time_slots?.length ?? 0) > 0 && (
        <SharingNote slots={data.time_slots ?? []} time={shownTime} zone={shownZone} />
      )}

      {data?.enabled && !hasFinnhub && (
        <Notice>
          It is on, with the keys stored on the server. You can turn it off here at any time. To
          change its keys, time, number of stocks, or what it refreshes, add your Finnhub key above
          first.
        </Notice>
      )}

      {data?.available && hasFinnhub && (
        <div className="stack" style={{ gap: 10 }}>
          <fieldset className="stack" style={{ gap: 8, border: 0, margin: 0, padding: 0 }}>
            <legend className="heading-with-info" style={{ padding: 0 }}>
              <span>What to refresh</span>
              <InfoTip topic="nightlyUniverse" />
            </legend>
            <div className="filter-row" style={{ alignItems: 'center' }}>
              {UNIVERSE_CHOICES.map((option) => {
                // A set another ticked set holds entirely is ticked with it, and stays so
                // until that one is unticked.
                const holder = heldBy(option.value, shownUniverses)
                const holderName = UNIVERSE_CHOICES.find((other) => other.value === holder)?.label
                return (
                  <label
                    key={option.value}
                    className="filter-row"
                    style={{ alignItems: 'center', gap: 6 }}
                  >
                    <input
                      type="checkbox"
                      checked={holder !== null || shownUniverses.includes(option.value)}
                      disabled={busy || holder !== null}
                      onChange={(event) =>
                        setUniverses(
                          UNIVERSE_CHOICES.map((choice) => choice.value).filter((value) =>
                            value === option.value
                              ? event.target.checked
                              : shownUniverses.includes(value),
                          ),
                        )
                      }
                    />
                    <span>
                      {option.label}
                      {holderName && <span className="muted"> (in {holderName})</span>}
                    </span>
                  </label>
                )
              })}
            </div>
            <div className="stack" style={{ gap: 6 }}>
              <div className="heading-with-info">
                <span>Also refresh these tickers</span>
                <InfoTip topic="nightlyCustom" />
              </div>
              {shownCustom.length > 0 && (
                <ul className="filter-row" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                  {shownCustom.map((symbol) => (
                    <li key={symbol} className="chip">
                      <span>{symbol}</span>
                      <button
                        type="button"
                        className="chip-remove"
                        aria-label={`Remove ${symbol}`}
                        disabled={busy}
                        onClick={() => setCustom(shownCustom.filter((other) => other !== symbol))}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {shownCustom.length < MAX_CUSTOM_SYMBOLS && (
                <div style={{ maxWidth: 360 }}>
                  <SearchBox
                    label="Add a stock or ETF to refresh"
                    placeholder="Add a ticker or company"
                    clearOnSelect
                    onSelect={(symbol) => {
                      const upper = symbol.toUpperCase()
                      if (!shownCustom.includes(upper)) setCustom([...shownCustom, upper])
                    }}
                  />
                </div>
              )}
            </div>
            {nothing && (
              <Notice tone="warning">
                Choose at least one set above or add a ticker, or there is nothing to refresh.
              </Notice>
            )}
          </fieldset>
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
          {shownKeys.length > 0 && (
            <div className="stack" style={{ gap: 8 }}>
              <div className="heading-with-info">
                <span>Price histories each key fetches a night</span>
                <InfoTip topic="nightlyPriceLimits" />
              </div>
              {shownKeys.map((key) => (
                <div key={key} className="stack" style={{ gap: 4 }}>
                  <span className="muted">{DATA_PROVIDER_NAMES[key]}</span>
                  <PresetOrCustom
                    label={`Stocks and ETFs ${DATA_PROVIDER_NAMES[key]} fetches price histories for`}
                    presets={PRICE_LIMIT_CHOICES[key]}
                    value={shownLimit(key)}
                    onChange={(value) => setLimits((now) => ({ ...now, [key]: value }))}
                    min={0}
                    max={ALL_THE_PLAN_ALLOWS - 1}
                    unit="stocks and ETFs, largest first"
                  />
                </div>
              ))}
            </div>
          )}
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
          Refreshing now, since {dateTime(latest.started_at)}. It takes from a few minutes to a few
          hours, depending on what it refreshes.
        </Notice>
      )}
      {latest?.status === 'succeeded' && (latest.notes ?? []).length > 0 && (
        <Notice>
          <p style={{ margin: 0 }}>The latest refresh, {dateTime(latest.started_at)}:</p>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {(latest.notes ?? []).map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
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

/** How many other refreshes start alongside one at `time` in `zone`, which split the
 * server's time with it, and the hour most others have chosen. */
function SharingNote({
  slots,
  time,
  zone,
}: {
  slots: NightlyTimeSlot[]
  time: string
  zone: string
}) {
  const mine = slotFor(slots, time, zone)
  const busiest = busiestSlot(slots)
  const count = (others: number) =>
    others === 1
      ? '1 other user’s refresh'
      : `${others.toLocaleString('en-US')} other users’ refreshes`
  return (
    <div className="stack" style={{ gap: 4 }}>
      <div className="heading-with-info">
        <strong>Sharing the server</strong>
        <InfoTip topic="nightlySharing" />
      </div>
      <p style={{ margin: 0, color: 'var(--text-secondary)' }}>
        {!mine
          ? 'Choose a time to see how many other refreshes start with yours.'
          : mine.others === 0
            ? `No other user’s refresh starts in the same hourly batch as yours (times from ${slotSpan(mine, zone)}), so yours would pay for all of its time.`
            : `${count(mine.others)} start in the same hourly batch as yours (times from ${slotSpan(mine, zone)}) and split the server’s time with it.`}{' '}
        {busiest && busiest !== mine
          ? `The most start for times from ${slotSpan(busiest, zone)} (${count(busiest.others)}).`
          : !busiest
            ? 'No one else refreshes yet.'
            : ''}{' '}
        Some times cost more or less than others, depending on whether yours is grouped with others.
        The default, 7:00 PM Eastern Time, gives the best chance of sharing, but not a sure one: how
        much sharing saves depends on how long the others run, and they can change their times.
      </p>
    </div>
  )
}

/** How a refresh's requests on one key compare with that key's plan, in words. */
function shareOf(use: NightlyKeyUse, report: ProviderUsage | undefined): string {
  if (use.provider === 'finnhub') {
    // The refresh sends 85% of the key's limit a minute, as Finnhub reports it (60 on the
    // free plan), so a paid plan's refresh takes, and costs, that much less.
    const limit = requestLimit(report, 'minute')?.limit || 60
    const minutes = Math.ceil(use.requests / Math.floor(limit * 0.85))
    return `No daily limit; paced under its ${limit.toLocaleString('en-US')} a minute, which takes about ${minutes.toLocaleString('en-US')} minutes.`
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
