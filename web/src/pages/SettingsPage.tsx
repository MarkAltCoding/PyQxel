import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteAccount, getAccount, updateCredentials, updateLimits } from '../api/endpoints'
import type { Account, CredentialsUpdate } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ErrorNotice, Notice } from '../components/Notice'
import { Stat } from '../components/Stat'
import { shortDate } from '../lib/format'

type Provider = 'anthropic_api_key' | 'fmp_api_key' | 'alpha_vantage_api_key'

const PROVIDERS: {
  field: Provider
  name: string
  account: 'anthropic' | 'fmp' | 'alpha_vantage'
  about: string
}[] = [
  {
    field: 'anthropic_api_key',
    name: 'Anthropic',
    account: 'anthropic',
    about: 'Writes AI research reports. Each report is billed to this key.',
  },
  {
    field: 'fmp_api_key',
    name: 'Financial Modeling Prep',
    account: 'fmp',
    about: 'Licensed market data: prices, quotes and company profiles, on your FMP plan.',
  },
  {
    field: 'alpha_vantage_api_key',
    name: 'Alpha Vantage',
    account: 'alpha_vantage',
    about:
      'Fills gaps: market data when FMP has none, company details FMP leaves empty, and financial statements SEC filings leave out. The free plan allows 25 requests a day.',
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
                current={account.data[provider.account]}
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

function KeyForm({
  provider,
  current,
  onSaved,
}: {
  provider: (typeof PROVIDERS)[number]
  current: Account['anthropic']
  onSaved: (account: Account) => void
}) {
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
      <p className="disclaimer">{provider.about}</p>
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
