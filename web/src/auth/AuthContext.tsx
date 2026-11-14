import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { configureClient } from '../api/client'
import { getAccount, renewSignIn, revokeSignIn } from '../api/endpoints'
import { clearProviderKeys } from '../lib/providerKeys'
import { stopSavedState } from '../lib/savedState'
import { readStored, writeStored } from '../lib/storage'
import { AuthContext } from './useAuth'

const STORAGE_KEY = 'pyqxel.apiKey'

/** How often a key in use is offered for renewal; the server renews it at most daily. */
const RENEW_EVERY_MS = 6 * 60 * 60 * 1000

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [apiKey, setApiKey] = useState<string | null>(() => {
    const stored = readStored(STORAGE_KEY)
    configureClient(stored)
    return stored
  })

  /** Drops the key from this browser, as when the API no longer accepts it. What was
   * entered here stays, for when the same person signs in again. */
  const forget = useCallback(() => {
    void stopSavedState(false)
    writeStored(STORAGE_KEY, null)
    configureClient(null)
    setApiKey(null)
    queryClient.clear()
  }, [queryClient])

  const signOut = useCallback(() => {
    // What was entered here is saved to the account one last time, while the key still
    // works, and then removed from this browser with the provider keys, so the next
    // person to use it neither sees nor spends them. Signing in again brings it back.
    void stopSavedState(true).finally(() => {
      // Best effort: signing out of this browser does not wait on the server.
      if (apiKey) revokeSignIn(apiKey).catch(() => {})
      clearProviderKeys()
      writeStored(STORAGE_KEY, null)
      configureClient(null)
      setApiKey(null)
      queryClient.clear()
    })
  }, [apiKey, queryClient])

  // A sign-in key lasts 30 days, so someone who keeps using PyQxel is kept signed in by
  // trading it for a new one: on opening, every few hours while open, and on coming
  // back to it. The server renews a key once it is a day old and leaves keys made for
  // scripts alone. A failure changes nothing; the next attempt may succeed.
  const lastRenewal = useRef(0)
  useEffect(() => {
    if (!apiKey) return
    let stopped = false
    const renew = () => {
      if (Date.now() - lastRenewal.current < RENEW_EVERY_MS) return
      lastRenewal.current = Date.now()
      renewSignIn(apiKey)
        .then(({ key }) => {
          if (stopped || !key) return
          // Kept only where the old one was, so "Keep me signed in" off still holds.
          if (readStored(STORAGE_KEY) === apiKey) writeStored(STORAGE_KEY, key)
          configureClient(key)
          setApiKey(key)
        })
        .catch(() => {})
    }
    renew()
    const timer = window.setInterval(renew, RENEW_EVERY_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible') renew()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [apiKey])

  // Configured during render, not in an effect, so the first queries already send the key.
  configureClient(apiKey, forget)

  const signIn = useCallback(async (key: string, remember: boolean) => {
    const trimmed = key.trim()
    const account = await getAccount(trimmed)
    writeStored(STORAGE_KEY, remember ? trimmed : null)
    configureClient(trimmed)
    setApiKey(trimmed)
    return account
  }, [])

  const value = useMemo(() => ({ apiKey, signIn, signOut }), [apiKey, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
