import { useSyncExternalStore } from 'react'

/** Below this width the app lays out for a phone: a tab bar, and cards for wide tables. */
export const PHONE_QUERY = '(max-width: 760px)'

/** Whether `query` matches now, kept up to date as the window changes; `false` where
 * media queries are unavailable, as in tests. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const media = window.matchMedia?.(query)
      media?.addEventListener('change', onChange)
      return () => media?.removeEventListener('change', onChange)
    },
    () => window.matchMedia?.(query).matches ?? false,
    () => false,
  )
}
