import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ApiError } from './api/client'
import { App } from './App'
import { AuthProvider } from './auth/AuthContext'
import { applyTheme, storedTheme } from './lib/theme'
import './styles/global.css'

applyTheme(storedTheme())

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      refetchOnWindowFocus: false,
      // Client errors (bad symbol, missing key, plan limits) will not fix themselves.
      retry: (failures, error) =>
        failures < 2 && !(error instanceof ApiError && error.status < 500),
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </QueryClientProvider>
  </StrictMode>,
)
