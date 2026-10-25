import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteAccount, getAccount, updateCredentials, updateLimits } from '../api/endpoints'
import type { Account, CredentialsUpdate } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice, Notice } from '../components/Notice'
import { Stat } from '../components/Stat'
import { shortDate } from '../lib/format'

type Provider = 'anthropic_api_key' | 'fmp_api_key' | 'alpha_vantage_api_key' | 'finnhub_api_key'
type ProviderAccount = 'anthropic' | 'fmp' | 'alpha_vantage' | 'finnhub'

interface ProviderInfo {
  field: Provider
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
    field: 'anthropic_api_key',
    name: 'Anthropic',
    account: 'anthropic',
    about: 'Writes AI research reports. Each report is billed to this key.',
    signUp: 'https://console.anthropic.com/settings/keys',
    free: 'Pay as you go; a report costs a few cents to a few tens of cents.',
    without: () => 'No AI research reports. Everything else works.',
  },
  {
    field: 'fmp_api_key',
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
        return "Price history comes from this server's development fallback (yfinance), which is unlicensed and can break without notice."
      }
      return `With neither this nor an Alpha Vantage key there is no price history: charts, volatility, factor exposures, backtests, portfolio tools and AI reports don't work.${has(account, 'finnhub') ? ' Finnhub still gives live quotes and company profiles.' : ''}`
    },
  },
  {
    field: 'alpha_vantage_api_key',
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
    field: 'finnhub_api_key',
    name: 'Finnhub',
    account: 'finnhub',
    about:
      'Company news, analyst ratings, earnings results and dates, and insider trades, on stock pages and in AI reports. Also the first source of live US quotes and a backup for company profiles.',
    signUp: 'https://finnhub.io/register',
    free: 'The free plan is enough: 60 requests a minute, US stocks.',
    without: (account) =>
      `No news, analyst ratings, earnings dates or insider trades, on stock pages or in AI reports.${has(account, 'fmp') ? " Live quotes use your FMP plan's daily allowance instead." : ''}`,
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
    name: 'AI research reports',
    works: (account) => has(account, 'anthropic') && hasPrices(account),
    needs: 'an Anthropic key, and a price source (Financial Modeling Prep or Alpha Vantage)',
  },
  {
    name: 'News, analyst ratings, earnings and insider trades',
    works: (account) => has(account, 'finnhub'),
    needs: 'a Finnhub key',
  },
  {
    name: 'Financial statements, valuation and SEC filings',
    works: (account) => account.sec_filings,
    needs: 'the server owner to set SEC_USER_AGENT; no key of yours',
  },
]

export function SettingsPage() {
  const queryClient = useQueryClient()
  const account = useQuery({ queryKey: ['account'], queryFn: () => getAccount() })
  const onSaved = (data: Account) => {
    queryClient.setQueryData(['account'], data)
    // Data and reports depend on the keys; refetch what was loaded with the old ones.
    void queryClient.invalidateQueries({ predicate: (query) => query.queryKey[0] !== 'account' })
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
          <section className="card stack" style={{ gap: 16 }}>
            <div>
              <h2>Provider keys</h2>
              <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
                You pay for your own usage. Keys are encrypted on the server, used only for your
                requests, and never shown again.
              </p>
            </div>
            {PROVIDERS.map((provider) => (
              <KeyForm
                key={provider.field}
                provider={provider}
                account={account.data}
                onSaved={onSaved}
              />
            ))}
          </section>
          <LimitsForm account={account.data} onSaved={onSaved} />
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
  onSaved,
}: {
  provider: ProviderInfo
  account: Account
  onSaved: (account: Account) => void
}) {
  const current = account[provider.account]
  const lost = current.configured ? null : provider.without(account)
  const [value, setValue] = useState('')
  const save = useMutation({
    mutationFn: (key: string | null) =>
      updateCredentials({ [provider.field]: key } as CredentialsUpdate),
    onSuccess: (data) => {
      setValue('')
      onSaved(data)
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (value.trim()) save.mutate(value.trim())
  }
  return (
    <form onSubmit={submit} className="stack" style={{ gap: 8 }}>
      <div className="filter-row" style={{ alignItems: 'center' }}>
        <h3>{provider.name}</h3>
        {current.configured ? (
          <span className="badge good">✓ Stored · ends {current.hint}</span>
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
        <button type="submit" className="button primary" disabled={!value.trim() || save.isPending}>
          Save
        </button>
        {current.configured && (
          <button
            type="button"
            className="button ghost danger"
            disabled={save.isPending}
            onClick={() => save.mutate(null)}
          >
            Remove
          </button>
        )}
      </div>
      {save.error !== null && <ErrorNotice error={save.error} />}
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
          Caps on new AI reports; leave blank for no cap. Reused reports are free and not counted.
        </p>
      </div>
      <div className="stats">
        <Stat label="Reports in the last hour" value={account.usage.last_hour} />
        <Stat label="Reports in the last day" value={account.usage.last_day} />
        <Stat
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

function DeleteAccount({ email }: { email: string }) {
  const { signOut } = useAuth()
  const [typed, setTyped] = useState('')
  const remove = useMutation({ mutationFn: deleteAccount, onSuccess: signOut })
  const confirmed = typed.trim().toLowerCase() === email.toLowerCase()
  return (
    <form
      className="card stack"
      style={{ gap: 14 }}
      onSubmit={(event) => {
        event.preventDefault()
        if (confirmed) remove.mutate()
      }}
    >
      <div>
        <h2>Delete account</h2>
        <p className="subtitle" style={{ color: 'var(--text-secondary)', marginTop: 2 }}>
          Deletes your account at once and for good: your provider keys, API keys, holdings,
          backtests, simulations and AI reports. Signing in again starts a new, empty account.
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
    </form>
  )
}
