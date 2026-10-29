import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteAccount, getAccount, updateAdminSettings, updateLimits } from '../api/endpoints'
import type { Account, AdminSettings, AdminSettingsUpdate } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorNotice, Notice } from '../components/Notice'
import { Stat } from '../components/Stat'
import { shortDate } from '../lib/format'
import { setProviderKey, type ProviderName } from '../lib/providerKeys'

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

const has = (account: Account, provider: ProviderAccount) => account[provider].configured

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
    name: 'Financial Modeling Prep',
    account: 'fmp',
    about: 'Licensed market data: prices, quotes and company profiles, on your FMP plan.',
    signUp: 'https://site.financialmodelingprep.com/developer/docs',
    free: 'The free plan allows 250 requests a day, and serves prices for a sample of large US stocks only.',
    without: (account) => {
      if (has(account, 'alpha_vantage')) {
        return 'Price history comes from Alpha Vantage alone: on its free plan, the latest 100 daily bars, not adjusted for splits or dividends, and 25 requests a day. Longer charts, five-year volatility models and long backtests lack history.'
      }
      if (account.market_data_fallback) {
        return 'Price history comes from yfinance, which is unlicensed and can break without notice.'
      }
      return `With neither this nor an Alpha Vantage key there is no price history: charts, volatility, factor exposures, backtests, portfolio tools and AI reports don't work.${has(account, 'finnhub') ? ' Finnhub still gives live quotes and company profiles.' : ''}`
    },
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
  has(account, 'fmp') || has(account, 'alpha_vantage') || account.market_data_fallback

const FEATURES: Feature[] = [
  {
    name: 'Price charts, volatility, factor exposures, backtests and portfolio tools',
    works: hasPrices,
    needs: 'a Financial Modeling Prep or Alpha Vantage key',
  },
  {
    name: 'Live quotes and company profiles',
    works: (account) => hasPrices(account) || has(account, 'finnhub'),
    needs: 'a Finnhub, Financial Modeling Prep or Alpha Vantage key',
  },
  {
    name: 'AI research reports and portfolio reviews',
    works: (account) => has(account, 'anthropic') && hasPrices(account),
    needs: 'an Anthropic key, and a price source (Financial Modeling Prep or Alpha Vantage)',
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
  const onSaved = (data: Account) => {
    queryClient.setQueryData(['account'], data)
    // Data and reports depend on the settings; refetch what was loaded with the old ones.
    void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'account' })
  }
  // The account reports the keys each request carries, so it is refetched too.
  const onKeysChanged = () => void queryClient.invalidateQueries()

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
          <section className="card stack" style={{ gap: 16 }}>
            <div>
              <h2>Provider keys</h2>
              <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
                You pay for your own usage. Keys stay in this browser and are sent with each of your
                requests; PyQxel's server never stores them, so no one else, not even whoever runs
                PyQxel, can read them. Add them on each device you use. Signing out removes them.
              </p>
            </div>
            {PROVIDERS.map((provider) => (
              <KeyForm
                key={provider.account}
                provider={provider}
                account={account.data}
                onChanged={onKeysChanged}
              />
            ))}
          </section>
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
    <section className="card stack" style={{ gap: 12 }}>
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

function KeyForm({
  provider,
  account,
  onChanged,
}: {
  provider: ProviderInfo
  account: Account
  onChanged: () => void
}) {
  const current = account[provider.account]
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
    </form>
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
    <section className="card stack" style={{ gap: 14 }}>
      <div>
        <h2>Admin settings</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Only your account sees these.
        </p>
      </div>
      <AdminSwitch
        label="Fall back to yfinance for my own requests"
        on={admin.yfinance_fallback || admin.yfinance_fallback_for_everyone || forced}
        disabled={save.isPending || admin.yfinance_fallback_for_everyone || forced}
        onChange={(on) => save.mutate({ yfinance_fallback: on })}
      />
      <AdminSwitch
        label="Fall back to yfinance for everyone"
        on={admin.yfinance_fallback_for_everyone || forced}
        disabled={save.isPending || forced}
        onChange={(on) => save.mutate({ yfinance_fallback_for_everyone: on })}
      />
      {forced && (
        <Notice tone="warning">
          YFINANCE_FALLBACK is on where the server is deployed, so yfinance is on for everyone
          whatever these say. Deploy with YFINANCE_FALLBACK=false to control it here.
        </Notice>
      )}
      <p className="disclaimer">
        yfinance fills the gaps in free FMP and Alpha Vantage plans (full adjusted price history,
        ETFs, indices, foreign listings, intraday bars and exchange rates) with Yahoo data, which is
        unlicensed and can break without notice. For yourself it is personal use. For everyone it
        also refreshes the screener with yfinance and Nasdaq data, and shows Yahoo&apos;s data to
        other people, which Yahoo&apos;s terms do not allow.
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
