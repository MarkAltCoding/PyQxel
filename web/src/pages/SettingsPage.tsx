import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteAccount,
  deleteStoredPrices,
  getAccount,
  getUsage,
  updateAdminSettings,
  updateLimits,
} from '../api/endpoints'
import type {
  Account,
  AdminSettings,
  AdminSettingsUpdate,
  NightlyRefreshStatus,
  ProviderUsage,
  UsageResponse,
} from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorNotice, Notice } from '../components/Notice'
import { Stat } from '../components/Stat'
import { shortDate } from '../lib/format'
import {
  coverage,
  DATA_FEATURES,
  DATA_PROVIDER_NAMES,
  hasKey,
  type DataProvider,
} from '../lib/keyCoverage'
import { InfoTip } from '../components/InfoTip'
import { NightlyRefreshCard } from '../components/NightlyRefreshCard'
import { NIGHTLY_QUERY_KEY, resyncNightlyKeys } from '../lib/nightlyRefresh'
import { setProviderKey, type ProviderName } from '../lib/providerKeys'
import { KeyUsage } from '../components/KeyUsage'
import {
  ASKING_PAGES,
  bytes,
  loadsWithoutAsking,
  planQuery,
  setLoadsWithoutAsking,
  setStoredPlan,
  storedPlans,
  USAGE_QUERY_KEY,
  type AskingPage,
} from '../lib/usage'

type ProviderAccount = ProviderName

interface ProviderInfo {
  name: string
  account: ProviderAccount
  about: string
  /** Where to sign up for a key. */
  signUp: string
  /** What the free plan gives. */
  free: string
  /** What is lost without this key, given the account's other keys; null when nothing is. */
  without: (account: Account) => string | null
}

const has = (account: Account, provider: ProviderAccount) => Boolean(account[provider]?.configured)

/** The user's price-history keys other than ``except``, by name. */
const otherPriceKeys = (account: Account, except: DataProvider) =>
  (['tiingo', 'fmp', 'eodhd', 'alpha_vantage'] as const)
    .filter((provider) => provider !== except && hasKey(account, provider))
    .map((provider) => DATA_PROVIDER_NAMES[provider])

const PROVIDERS: ProviderInfo[] = [
  {
    name: 'Anthropic',
    account: 'anthropic',
    about:
      'Writes AI research reports and portfolio reviews, and answers the strategy coding assistant. Each is billed to this key.',
    signUp: 'https://console.anthropic.com/settings/keys',
    free: 'Pay as you go; a report costs a few cents to a few tens of cents.',
    without: () =>
      'No AI research reports, portfolio reviews or coding assistant. Everything else works.',
  },
  {
    name: 'Tiingo',
    account: 'tiingo',
    about:
      'The first source of price history for US stocks, ETFs and funds: 30+ years, adjusted for splits and dividends. Also intraday charts, live US quotes and company names.',
    signUp: 'https://www.tiingo.com/account/api/token',
    free: 'The free plan covers most people: 30+ years of daily prices, 500 different symbols a month, 50 requests an hour.',
    without: (account) => {
      const others = otherPriceKeys(account, 'tiingo')
      return others.length
        ? `US price history comes from ${others.join(' and ')}, which on free plans cover less: see what works above. Intraday charts need a paid key.`
        : 'No US price history unless you add another price key: charts, volatility, factor exposures, backtests, portfolio tools and AI reports need one. A free Tiingo key is the simplest.'
    },
  },
  {
    name: 'Financial Modeling Prep',
    account: 'fmp',
    about:
      'Company profiles (sector, industry, market cap), quotes and index quotes, and price history on paid plans.',
    signUp: 'https://site.financialmodelingprep.com/developer/docs',
    free: 'The free plan allows 250 requests a day, and serves prices for a sample of large US stocks only.',
    without: (account) => {
      if (account.market_data_fallback && otherPriceKeys(account, 'fmp').length === 0) {
        return 'Price history comes from yfinance, which is unlicensed and can break without notice.'
      }
      return has(account, 'finnhub')
        ? 'Sectors come from your other keys where they can: Finnhub gives industries and market caps, and Tiingo sectors for the Dow 30 only.'
        : 'No sector names or market caps for most stocks unless Finnhub or a paid key gives them.'
    },
  },
  {
    name: 'EODHD',
    account: 'eodhd',
    about:
      'Index history such as the S&P 500, listings outside the US, and delayed quotes on every exchange.',
    signUp: 'https://eodhd.com/register',
    free: 'The free plan gives the last year of prices on every exchange, 20 requests a day; its $19.99 Historian plan gives 30+ years.',
    without: () =>
      'No index history, and no prices for listings outside the US, unless another key covers them: see what works above.',
  },
  {
    name: 'Alpha Vantage',
    account: 'alpha_vantage',
    about:
      'Fills gaps: market data when FMP has none, company details FMP leaves empty, and financial statements SEC filings leave out.',
    signUp: 'https://www.alphavantage.co/support/#api-key',
    free: 'The free plan allows 25 requests a day; answers are reused for up to a day to make them last.',
    without: (account) =>
      has(account, 'fmp')
        ? "No backup when FMP fails or your FMP plan doesn't cover a stock, and financial statement items SEC filings leave out stay empty."
        : "Financial statement items SEC filings leave out stay empty, and there's no second source of price history.",
  },
  {
    name: 'Finnhub',
    account: 'finnhub',
    about:
      'Company news, analyst ratings, earnings, insider trading, key metrics, lobbying, government contracts and patents on stock pages; market news and the earnings, IPO and holiday calendar on the Markets tab; all of it in AI reports. Also the first source of live US quotes, and a backup for company profiles and for financial statements when SEC EDGAR cannot be read.',
    signUp: 'https://finnhub.io/register',
    free: 'The free plan is enough: 60 requests a minute, US stocks.',
    without: (account) =>
      `No company or market news, analyst ratings, earnings dates, insider trades, key metrics, lobbying, government contracts or patents, on stock pages, the Markets tab or in AI reports; the Markets calendar shows FOMC meetings only.${has(account, 'fmp') ? " Live quotes use your FMP plan's daily allowance instead." : ''}`,
  },
]

interface Feature {
  name: string
  works: (account: Account) => boolean
  needs: string
}

const hasPrices = (account: Account) =>
  (['tiingo', 'fmp', 'eodhd', 'alpha_vantage'] as const).some((provider) =>
    hasKey(account, provider),
  ) || account.market_data_fallback

const FEATURES: Feature[] = [
  {
    name: 'Price charts, volatility, factor exposures, backtests and portfolio tools',
    works: hasPrices,
    needs:
      'a price key: Tiingo (free) is the simplest, or Financial Modeling Prep, EODHD or Alpha Vantage',
  },
  {
    name: 'Live quotes and company profiles',
    works: (account) => hasPrices(account) || has(account, 'finnhub'),
    needs: 'a Finnhub or Tiingo key (both free), or another price key',
  },
  {
    name: 'AI research reports and portfolio reviews',
    works: (account) => has(account, 'anthropic') && hasPrices(account),
    needs: 'an Anthropic key, and a price key (Tiingo is free)',
  },
  {
    name: 'AI coding assistant for custom strategies',
    works: (account) => has(account, 'anthropic'),
    needs: 'an Anthropic key',
  },
  {
    name: 'News, analyst ratings, earnings, insider trades and key metrics',
    works: (account) => has(account, 'finnhub'),
    needs: 'a Finnhub key',
  },
  {
    name: 'Lobbying, government contracts, patents and recent SEC filings',
    works: (account) => has(account, 'finnhub'),
    needs: 'a Finnhub key',
  },
  {
    name: 'Market news, and earnings, IPOs and holidays on the Markets calendar',
    works: (account) => has(account, 'finnhub'),
    needs: 'a Finnhub key (FOMC meetings show without one)',
  },
  {
    name: 'Financial statements and valuation',
    works: (account) => account.sec_filings || has(account, 'finnhub'),
    needs: 'the server owner to set SEC_USER_AGENT, or a Finnhub key',
  },
  {
    name: 'SEC filing text (Risk Factors and MD&A) in AI reports',
    works: (account) => account.sec_filings,
    needs: 'the server owner to set SEC_USER_AGENT; no key of yours',
  },
]

export function SettingsPage() {
  const queryClient = useQueryClient()
  const account = useQuery({ queryKey: ['account'], queryFn: () => getAccount() })
  const [plans, setPlans] = useState(storedPlans)
  const usage = useQuery({
    queryKey: [USAGE_QUERY_KEY, plans],
    queryFn: () => getUsage(planQuery(plans)),
    enabled: account.data !== undefined,
  })
  const choosePlan = (provider: string, plan: string) => {
    setStoredPlan(provider, plan)
    setPlans(storedPlans())
  }
  const reportOf = (provider: ProviderName): ProviderUsage | undefined =>
    usage.data?.providers.find((report) => report.provider === provider)
  const onSaved = (data: Account) => {
    queryClient.setQueryData(['account'], data)
    // Data and reports depend on the settings; refetch what was loaded with the old ones.
    void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'account' })
  }
  // The account reports the keys each request carries, so it is refetched too. A nightly
  // refresh the user opted in to gets the changed keys, so its stored copy matches.
  const onKeysChanged = () => {
    const nightly = queryClient.getQueryData<NightlyRefreshStatus>(NIGHTLY_QUERY_KEY)
    void resyncNightlyKeys(nightly)
      .catch(() => undefined)
      .finally(() => void queryClient.invalidateQueries())
  }

  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Settings</h1>
          <div className="subtitle">
            {account.data
              ? `${account.data.email} · member since ${shortDate(account.data.created_at)}`
              : 'Your account'}
          </div>
        </div>
      </header>
      {account.error !== null && <ErrorNotice error={account.error} />}
      {account.data && (
        <>
          <FeatureSummary account={account.data} />
          <DataCoverage account={account.data} />
          <section id="provider-keys" className="card stack" style={{ gap: 16 }}>
            <div>
              <h2>Provider keys</h2>
              <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
                You pay for your own usage. Keys stay in this browser and are sent with each of your
                requests; PyQxel's server does not store them, so no one else, not even whoever runs
                PyQxel, can read them, unless you turn on the nightly screener refresh below. Add
                them on each device you use. Signing out removes them from this browser.
              </p>
            </div>
            {PROVIDERS.map((provider) => (
              <KeyForm
                key={provider.account}
                provider={provider}
                account={account.data}
                onChanged={onKeysChanged}
                report={reportOf(provider.account)}
                onPlan={(plan) => choosePlan(provider.account, plan)}
              />
            ))}
            {usage.error !== null && <ErrorNotice error={usage.error} />}
          </section>
          <UsageSettings usage={usage.data} />
          <NightlyRefreshCard account={account.data} usage={usage.data} />
          <LimitsForm account={account.data} onSaved={onSaved} />
          {account.data.admin && <AdminSettingsForm admin={account.data.admin} onSaved={onSaved} />}
          <DeleteAccount email={account.data.email} />
        </>
      )}
    </div>
  )
}

function FeatureSummary({ account }: { account: Account }) {
  const missing = FEATURES.filter((feature) => !feature.works(account))
  return (
    <section id="key-coverage" className="card stack" style={{ gap: 12 }}>
      <div>
        <h2>What works with your keys</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          {missing.length === 0
            ? 'Everything is available.'
            : 'PyQxel works with any of the keys below; each one you add turns more on.'}
        </p>
      </div>
      <ul className="feature-list">
        {FEATURES.map((feature) => {
          const works = feature.works(account)
          return (
            <li key={feature.name}>
              <span className={`badge ${works ? 'good' : 'warning'}`}>
                {works ? '✓ On' : '! Off'}
              </span>
              <span>
                {feature.name}
                {!works && <span className="feature-needs"> · needs {feature.needs}</span>}
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

const LEVELS = {
  full: { label: '✓ Covered', tone: 'good' },
  fallback: { label: '✓ Covered', tone: 'good' },
  partial: { label: '◐ Partly', tone: 'warning' },
  none: { label: '! Off', tone: 'warning' },
} as const

/** For each kind of market data: which of the user's keys give it, and the cheapest key
 * that would add more. */
function DataCoverage({ account }: { account: Account }) {
  return (
    <section id="data-coverage" className="card stack" style={{ gap: 12 }}>
      <div>
        <div className="heading-with-info">
          <h2>Market data, by kind</h2>
          <InfoTip topic="dataCoverage" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Each kind of data comes from whichever of your keys covers it, cheapest first. Free plans
          are assumed; a paid plan of yours covers more where it says so.
        </p>
      </div>
      <ul className="coverage-list">
        {DATA_FEATURES.map((feature) => {
          const { level, from, next } = coverage(feature, account)
          return (
            <li key={feature.id}>
              <div className="coverage-head">
                <span className={`badge ${LEVELS[level].tone}`}>{LEVELS[level].label}</span>
                <strong>{feature.name}</strong>
              </div>
              {from.length > 0 ? (
                <ul className="coverage-sources">
                  {from.map((step) => (
                    <li key={step.provider}>
                      <strong>{DATA_PROVIDER_NAMES[step.provider]}</strong>:{' '}
                      {step.free ? `free plan, ${step.free}` : 'needs a paid plan'}
                      {step.paid && <span className="muted"> · paid: {step.paid}</span>}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted">
                  {level === 'fallback'
                    ? 'None of your keys covers it; the server fills it in for your account.'
                    : 'None of your keys covers it.'}
                </p>
              )}
              {next && (
                <p className="coverage-next">
                  Add {/^[AEIOU]/.test(DATA_PROVIDER_NAMES[next.provider]) ? 'an' : 'a'}{' '}
                  {DATA_PROVIDER_NAMES[next.provider]} key:{' '}
                  {next.free ? `its free plan gives ${next.free}` : 'it needs a paid plan'}
                  {next.paid ? `; paid, ${next.paid}` : ''}.
                </p>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function KeyForm({
  provider,
  account,
  onChanged,
  report,
  onPlan,
}: {
  provider: ProviderInfo
  account: Account
  onChanged: () => void
  /** The key's plan usage, once read. */
  report?: ProviderUsage
  onPlan: (plan: string) => void
}) {
  // A server from before a provider was added sends nothing for it: no key was added.
  const current = account[provider.account] ?? { configured: false, hint: null }
  const lost = current.configured ? null : provider.without(account)
  const [value, setValue] = useState('')
  const save = (key: string | null) => {
    setProviderKey(provider.account, key)
    setValue('')
    onChanged()
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (value.trim()) save(value.trim())
  }
  return (
    <form onSubmit={submit} className="stack" style={{ gap: 8 }}>
      <div className="filter-row" style={{ alignItems: 'center' }}>
        <h3>{provider.name}</h3>
        {current.configured ? (
          <span className="badge good">✓ In this browser · ends {current.hint}</span>
        ) : (
          <span className="badge warning">! Not set</span>
        )}
      </div>
      <p className="disclaimer">
        {provider.about} {provider.free}{' '}
        <a href={provider.signUp} target="_blank" rel="noopener noreferrer">
          Get a key
        </a>
      </p>
      {lost && (
        <Notice tone="warning">
          <strong>Without this key:</strong> {lost}
        </Notice>
      )}
      <div className="filter-row">
        <label className="field" style={{ flex: 1, maxWidth: 420 }}>
          <span className="visually-hidden">{provider.name} API key</span>
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            placeholder={current.configured ? 'Replace with a new key' : 'Paste your key'}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </label>
        <button type="submit" className="button primary" disabled={!value.trim()}>
          Save
        </button>
        {current.configured && (
          <button type="button" className="button ghost danger" onClick={() => save(null)}>
            Remove
          </button>
        )}
      </div>
      {current.configured && report && <KeyUsage report={report} onPlan={onPlan} />}
    </form>
  )
}

/** Which pages ask before calling providers, and the prices PyQxel keeps for the user. */
function UsageSettings({ usage }: { usage: UsageResponse | undefined }) {
  const queryClient = useQueryClient()
  const [, setChanged] = useState(0)
  const [confirming, setConfirming] = useState(false)
  const remove = useMutation({
    mutationFn: deleteStoredPrices,
    onSuccess: () => {
      setConfirming(false)
      void queryClient.invalidateQueries({ queryKey: [USAGE_QUERY_KEY] })
    },
  })
  const stored = usage?.stored_prices
  return (
    <section id="plan-usage" className="card stack" style={{ gap: 14 }}>
      <div>
        <div className="heading-with-info">
          <h2>What spends your plans’ limits</h2>
          <InfoTip topic="loadEstimate" />
        </div>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Pages that call your keys’ providers as soon as they open ask first, saying about how many
          requests each key would spend. Untick a page to load it without asking.
        </p>
      </div>
      <div className="stack" style={{ gap: 6 }}>
        {(Object.keys(ASKING_PAGES) as AskingPage[]).map((page) => (
          <label key={page} className="filter-row" style={{ alignItems: 'center', gap: 10 }}>
            <input
              type="checkbox"
              checked={!loadsWithoutAsking(page)}
              onChange={(event) => {
                setLoadsWithoutAsking(page, !event.target.checked)
                setChanged((count) => count + 1)
              }}
            />
            <span>Ask before loading {ASKING_PAGES[page]}</span>
          </label>
        ))}
      </div>
      <div className="stats">
        <Stat
          info="storedPrices"
          label="Stored prices"
          value={stored ? `${stored.symbols.toLocaleString('en-US')} symbols` : '–'}
          detail={stored ? bytes(stored.bytes) : undefined}
        />
      </div>
      {stored && stored.histories > 0 && (
        <div>
          <button type="button" className="button ghost danger" onClick={() => setConfirming(true)}>
            Delete stored prices
          </button>
        </div>
      )}
      {confirming && (
        <ConfirmDialog
          title="Delete your stored prices?"
          confirmLabel="Delete them"
          busy={remove.isPending}
          onConfirm={() => remove.mutate()}
          onCancel={() => setConfirming(false)}
        >
          Pages and backtests will fetch prices again on your keys, spending a request for each
          symbol.
        </ConfirmDialog>
      )}
      {remove.error !== null && <ErrorNotice error={remove.error} />}
    </section>
  )
}

function LimitsForm({ account, onSaved }: { account: Account; onSaved: (a: Account) => void }) {
  const [hour, setHour] = useState(String(account.limits.ai_requests_per_hour ?? ''))
  const [day, setDay] = useState(String(account.limits.ai_requests_per_day ?? ''))
  const save = useMutation({
    mutationFn: () =>
      updateLimits({
        ai_requests_per_hour: hour.trim() ? Number(hour) : null,
        ai_requests_per_day: day.trim() ? Number(day) : null,
      }),
    onSuccess: onSaved,
  })
  return (
    <form
      id="ai-limits"
      className="card stack"
      style={{ gap: 14 }}
      onSubmit={(event) => {
        event.preventDefault()
        save.mutate()
      }}
    >
      <div>
        <h2>AI spending limits</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Caps on new AI requests: reports, portfolio reviews and assistant answers. Leave blank for
          no cap; reused reports are free and not counted.
        </p>
      </div>
      <div className="stats">
        <Stat
          info="aiRequestsHour"
          label="AI requests in the last hour"
          value={account.usage.last_hour}
        />
        <Stat
          info="aiRequestsDay"
          label="AI requests in the last day"
          value={account.usage.last_day}
        />
        <Stat
          info="requestsPerMinute"
          label="Requests per minute"
          value={account.rate_limit_per_minute}
          detail="Set by the server"
        />
      </div>
      <div className="filter-row">
        <label className="field" style={{ width: 160 }}>
          <span>Per hour</span>
          <input type="number" min={1} value={hour} onChange={(e) => setHour(e.target.value)} />
        </label>
        <label className="field" style={{ width: 160 }}>
          <span>Per day</span>
          <input type="number" min={1} value={day} onChange={(e) => setDay(e.target.value)} />
        </label>
        <button type="submit" className="button primary" disabled={save.isPending}>
          Save limits
        </button>
      </div>
      {save.isSuccess && <Notice>Limits saved.</Notice>}
      {save.error !== null && <ErrorNotice error={save.error} />}
    </form>
  )
}

/** Shown only to the account ADMIN_EMAIL names; the server refuses everyone else. */
function AdminSettingsForm({
  admin,
  onSaved,
}: {
  admin: AdminSettings
  onSaved: (account: Account) => void
}) {
  const save = useMutation({
    mutationFn: (changes: AdminSettingsUpdate) => updateAdminSettings(changes),
    onSuccess: onSaved,
  })
  const forced = admin.yfinance_fallback_from_server
  return (
    <section id="admin" className="card stack" style={{ gap: 14 }}>
      <div>
        <h2>Admin settings</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Only your account sees these.
        </p>
      </div>
      <AdminSwitch
        label="Fall back to yfinance for my own requests"
        on={admin.yfinance_fallback || forced}
        disabled={save.isPending || forced}
        onChange={(on) => save.mutate({ yfinance_fallback: on })}
      />
      {forced && (
        <Notice tone="warning">
          YFINANCE_FALLBACK is on where the server is deployed, so yfinance is on for your requests
          whatever this says. Deploy with YFINANCE_FALLBACK=false to control it here.
        </Notice>
      )}
      <p className="disclaimer">
        yfinance fills the gaps in free FMP and Alpha Vantage plans (full adjusted price history,
        ETFs, indices, foreign listings, intraday bars and exchange rates) with Yahoo data, which is
        unlicensed and can break without notice, so it is for your personal use only. It never
        reaches another account: their requests use their own keys alone, and a screener refreshed
        with yfinance and Nasdaq data is shown to you alone.
      </p>
      {save.error !== null && <ErrorNotice error={save.error} />}
    </section>
  )
}

function AdminSwitch({
  label,
  on,
  disabled,
  onChange,
}: {
  label: string
  on: boolean
  disabled: boolean
  onChange: (on: boolean) => void
}) {
  return (
    <label className="filter-row" style={{ alignItems: 'center', gap: 10 }}>
      <input
        type="checkbox"
        checked={on}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>{label}</span>
      <span className={`badge ${on ? 'good' : 'warning'}`}>{on ? '✓ On' : '! Off'}</span>
    </label>
  )
}

function DeleteAccount({ email }: { email: string }) {
  const { signOut } = useAuth()
  const [typed, setTyped] = useState('')
  const [asking, setAsking] = useState(false)
  const remove = useMutation({ mutationFn: deleteAccount, onSuccess: signOut })
  const confirmed = typed.trim().toLowerCase() === email.toLowerCase()
  return (
    <form
      id="delete-account"
      className="card stack"
      style={{ gap: 14 }}
      onSubmit={(event) => {
        event.preventDefault()
        if (confirmed) setAsking(true)
      }}
    >
      <div>
        <h2>Delete account</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Deletes your account at once and for good: your API keys, holdings, backtests, simulations
          and AI reports. Signing in again starts a new, empty account.
        </p>
      </div>
      <div className="filter-row">
        <label className="field" style={{ width: 320 }}>
          <span>Type {email} to confirm</span>
          <input
            type="email"
            autoComplete="off"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
        </label>
        <button type="submit" className="button danger" disabled={!confirmed || remove.isPending}>
          Delete account
        </button>
      </div>
      {remove.error !== null && <ErrorNotice error={remove.error} />}
      {asking && (
        <ConfirmDialog
          title="Delete your account?"
          confirmLabel="Delete my account"
          busy={remove.isPending}
          onCancel={() => setAsking(false)}
          onConfirm={() => remove.mutate(undefined, { onSettled: () => setAsking(false) })}
        >
          <p>
            Are you sure? This deletes the account for <strong>{email}</strong> at once, and it
            cannot be undone. You will lose:
          </p>
          <ul>
            <li>Your saved portfolio holdings</li>
            <li>Your saved backtests, including the code of your custom strategies</li>
            <li>Your Monte Carlo simulations</li>
            <li>Your AI research reports and portfolio reviews, including ones you pasted</li>
            <li>Your PyQxel API keys: any script using them stops working</li>
            <li>The link between this account and your GitHub or Google sign-in</li>
            <li>
              Your nightly screener refresh, the keys it stored on the server and the screener it
              built
            </li>
            <li>
              The provider keys kept in this browser (Anthropic, FMP, Alpha Vantage, Finnhub), which
              are removed from it. The keys themselves stay valid with those services.
            </li>
          </ul>
          <p>Signing in again later starts a new, empty account.</p>
        </ConfirmDialog>
      )}
    </form>
  )
}
