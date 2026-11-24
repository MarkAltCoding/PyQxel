import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getAccount, getUsage } from '../api/endpoints'
import { NightlyRefreshCard } from '../components/NightlyRefreshCard'
import { ErrorNotice } from '../components/Notice'
import { ScreenerTabs } from '../components/ScreenerTabs'
import { planQuery, storedPlans, USAGE_QUERY_KEY } from '../lib/usage'

/** The Screener's Refresh Schedule tab: when your own screener is rebuilt from your keys.
 * What it costs is paid for in Settings' Billing card, which this links to. */
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
            When your own screener is rebuilt from your keys. It is paid for under{' '}
            <Link to="/settings#billing">Billing</Link> in Settings.
          </div>
        </div>
        <ScreenerTabs />
      </header>
      {account.error !== null && <ErrorNotice error={account.error} />}
      {account.data && <NightlyRefreshCard account={account.data} usage={usage.data} />}
    </div>
  )
}
