// Asking before a page calls the user's keys' providers: what loading would cost, and
// whether the user has agreed.
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { estimateUsage } from '../api/endpoints'
import type { PlannedFetch } from '../api/types'
import {
  agree,
  agreedTo,
  loadsWithoutAsking,
  setLoadsWithoutAsking,
  storedPlans,
  type AskingPage,
} from './usage'

export type LoadGateState = ReturnType<typeof useLoadGate>

/**
 * Whether a page may load what calls providers: at once when nothing would be spent, when
 * the user agreed to the same thing earlier in this tab, or when they chose to load
 * `page` without asking; otherwise once they agree. `signature` names what the page would
 * show, e.g. its symbols, so agreeing covers that alone.
 */
export function useLoadGate(
  page: AskingPage,
  signature: string,
  fetches: PlannedFetch[],
  ready = true,
) {
  const key = `${page}:${signature}`
  const [, setAgreed] = useState(0)
  const free = loadsWithoutAsking(page) || agreedTo(key)
  const estimate = useQuery({
    queryKey: ['usage-estimate', key],
    queryFn: () => estimateUsage(fetches, storedPlans()),
    enabled: ready && !free,
    staleTime: 30_000,
  })
  const spends = (estimate.data?.providers ?? []).some((p) => p.requests > 0)
  const allowed = free || (estimate.data !== undefined && !spends)
  const load = (always: boolean) => {
    if (always) setLoadsWithoutAsking(page, true)
    agree(key)
    setAgreed((count) => count + 1)
  }
  return {
    page,
    allowed,
    checking: !allowed && (!ready || estimate.isLoading),
    estimate,
    load,
  }
}
