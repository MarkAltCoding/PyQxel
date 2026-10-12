import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { configureClient } from '../api/client'
import { getAccount } from '../api/endpoints'
import { readStored, writeStored } from '../lib/storage'
import { AuthContext } from './useAuth'

const STORAGE_KEY = 'pyqxel.apiKey'

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const [apiKey, setApiKey] = useState<string | null>(() => {
    const stored = readStored(STORAGE_KEY)
    configureClient(stored)
    return stored
  })

  const signOut = useCallback(() => {
    writeStored(STORAGE_KEY, null)
    configureClient(null)
    setApiKey(null)
    queryClient.clear()
  }, [queryClient])

  // Configured during render, not in an effect, so the first queries already send the key.
  configureClient(apiKey, signOut)

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
