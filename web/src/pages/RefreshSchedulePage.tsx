import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getAccount, getUsage } from '../api/endpoints'
import { BillingCard } from '../components/BillingCard'
import { NightlyRefreshCard } from '../components/NightlyRefreshCard'
import { ErrorNotice } from '../components/Notice'
import { ScreenerTabs } from '../components/ScreenerTabs'
import { planQuery, storedPlans, USAGE_QUERY_KEY } from '../lib/usage'

/** The Screener's Refresh Schedule tab: when your own screener is rebuilt, and how it is
 * paid for. It is the same card as in Settings, with the same settings. */
export function RefreshSchedulePage() {
  const account = useQuery({ queryKey: ['account'], queryFn: () => getAccount() })
  const plans = storedPlans()
  const usage = useQuery({
    queryKey: [USAGE_QUERY_KEY, plans],
    queryFn: () => getUsage(planQuery(plans)),
    enabled: account.data !== undefined,
  })
  return (
    <div className="stack">
      <header className="page-header">
        <div>
          <h1>Refresh Schedule</h1>
          <div className="subtitle">
            When your own screener is rebuilt from your keys, and what it costs. The same settings
            are in <Link to="/settings#nightly-refresh">Settings</Link>.
          </div>
        </div>
        <ScreenerTabs />
      </header>
      {account.error !== null && <ErrorNotice error={account.error} />}
      {account.data && (
        <>
          <NightlyRefreshCard account={account.data} usage={usage.data} />
          <BillingCard />
        </>
      )}
    </div>
  )
}
