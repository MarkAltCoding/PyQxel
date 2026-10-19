import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './auth/AuthContext'
import { routes } from './routes'

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return router
}

afterEach(() => vi.restoreAllMocks())

type Route = (init: RequestInit | undefined) => Response | Promise<Response>

/** Answers fetches by path, recording each one; paths without a route get 404. */
function serve(routes: Record<string, Route>) {
  const calls: { path: string; method: string; authorization?: string }[] = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const path = new URL(String(input), 'http://localhost').pathname
    const headers = (init?.headers ?? {}) as Record<string, string>
    calls.push({ path, method: init?.method ?? 'GET', authorization: headers.Authorization })
    const route = routes[path]
    return route ? route(init) : new Response('{}', { status: 404 })
  })
  return calls
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status })
const PROVIDERS = [
  { name: 'github', label: 'GitHub' },
  { name: 'google', label: 'Google' },
]

describe('sign-in', () => {
  it('sends visitors without a key to sign in, then back where they were going', async () => {
    const calls = serve({
      '/api/v1/auth/providers': () => json([]),
      '/api/v1/me': () => json({ email: 'a@b.c' }),
    })
    const router = renderAt('/screener')

    expect(router.state.location.pathname).toBe('/signin')
    await userEvent.type(screen.getByLabelText('API key'), 'pq_good')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(router.state.location.pathname).toBe('/screener')
    expect(calls.find((call) => call.path === '/api/v1/me')?.authorization).toBe('Bearer pq_good')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_good')
  })

  it('explains a rejected key and stays on the sign-in page', async () => {
    serve({
      '/api/v1/auth/providers': () => json([]),
      '/api/v1/me': () => json({ detail: 'Invalid API key.' }, 401),
    })
    const router = renderAt('/')

    await userEvent.type(screen.getByLabelText('API key'), 'pq_bad')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid API key.')
    expect(router.state.location.pathname).toBe('/signin')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
  })

  it('offers GitHub and Google, returning to the page the visitor wanted', async () => {
    serve({ '/api/v1/auth/providers': () => json(PROVIDERS) })
    renderAt('/backtest')

    const github = await screen.findByRole('link', { name: 'Continue with GitHub' })
    const google = screen.getByRole('link', { name: 'Continue with Google' })

    expect(github).toHaveAttribute('href', '/api/v1/auth/github/start?next=%2Fbacktest')
    expect(google).toHaveAttribute('href', '/api/v1/auth/google/start?next=%2Fbacktest')
    expect(screen.getByText('New here? Signing in creates your account.')).toBeInTheDocument()
  })

  it('finishes a GitHub or Google sign-in from the key it sends back', async () => {
    const calls = serve({
      '/api/v1/auth/providers': () => json(PROVIDERS),
      '/api/v1/me': () => json({ email: 'octo@example.com' }),
    })
    const router = renderAt('/signin#key=pq_signed_in&next=%2Fbacktest%3Fsymbol%3DSPY')

    await waitFor(() => expect(router.state.location.pathname).toBe('/backtest'))
    expect(router.state.location.search).toBe('?symbol=SPY')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_signed_in')
    expect(calls.find((call) => call.path === '/api/v1/me')?.authorization).toBe(
      'Bearer pq_signed_in',
    )
    expect(window.location.hash).not.toContain('pq_signed_in')
  })

  it('finishes a sign-in that comes back to a sign-in page already open', async () => {
    serve({
      '/api/v1/auth/providers': () => json(PROVIDERS),
      '/api/v1/me': () => json({ email: 'octo@example.com' }),
    })
    const router = renderAt('/signin')
    await screen.findByRole('link', { name: 'Continue with GitHub' })

    await router.navigate('/signin#key=pq_later&next=%2Fscreener')

    await waitFor(() => expect(router.state.location.pathname).toBe('/screener'))
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_later')
  })

  it('shows why signing in with a provider failed', async () => {
    serve({ '/api/v1/auth/providers': () => json(PROVIDERS) })
    renderAt('/signin#error=Signing+in+with+GitHub+was+cancelled.')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Signing in with GitHub was cancelled.',
    )
  })

  it('revokes the key on the server when signing out', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_session')
    const calls = serve({
      '/api/v1/auth/providers': () => json(PROVIDERS),
      '/api/v1/auth/signout': () => new Response(null, { status: 204 }),
    })
    const router = renderAt('/settings')

    await userEvent.click(await screen.findByRole('button', { name: 'Sign out' }))

    expect(router.state.location.pathname).toBe('/signin')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
    expect(calls).toContainEqual({
      path: '/api/v1/auth/signout',
      method: 'POST',
      authorization: 'Bearer pq_session',
    })
  })

  it('forgets a key the server no longer accepts, without asking to revoke it', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_revoked')
    const calls = serve({
      '/api/v1/auth/providers': () => json([]),
      '/api/v1/me': () => json({ detail: 'Invalid API key.' }, 401),
    })
    const router = renderAt('/settings')

    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'))
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
    expect(calls.some((call) => call.path === '/api/v1/auth/signout')).toBe(false)
  })

  it('shows the privacy policy without signing in', async () => {
    serve({ '/api/v1/auth/providers': () => json(PROVIDERS) })
    const router = renderAt('/privacy')

    expect(await screen.findByRole('heading', { name: 'Privacy policy' })).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/privacy')
    expect(screen.getByText(/never sells data/)).toBeInTheDocument()
  })
})
