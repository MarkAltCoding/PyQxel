import { createContext, useContext } from 'react'
import type { Account } from '../api/types'

export interface Auth {
  apiKey: string | null
  /** Checks `key` against the API, then keeps it; throws ApiError when it is refused. */
  signIn: (key: string, remember: boolean) => Promise<Account>
  signOut: () => void
}

export const AuthContext = createContext<Auth | null>(null)

export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside AuthProvider.')
  return auth
}
