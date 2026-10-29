import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from './auth/AuthContext'
import { clearProviderKeys, providerKeyHeaders, setProviderKey } from './lib/providerKeys'
import { markSignInStarted, signInStartedRecently } from './auth/pendingSignIn'
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
    markSignInStarted()
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
    // Clicking starts the sign-in in this tab; the test stays on the page instead of leaving.
    const github = await screen.findByRole('link', { name: 'Continue with GitHub' })
    github.addEventListener('click', (event) => event.preventDefault())
    await userEvent.click(github)

    await router.navigate('/signin#key=pq_later&next=%2Fscreener')

    await waitFor(() => expect(router.state.location.pathname).toBe('/screener'))
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_later')
  })

  it('shows why signing in with a provider failed', async () => {
    serve({ '/api/v1/auth/providers': () => json(PROVIDERS) })
    markSignInStarted()
    renderAt('/signin#error=Signing+in+with+GitHub+was+cancelled.')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Signing in with GitHub was cancelled.',
    )
  })

  it('ignores a key in a link this tab did not start a sign-in for', async () => {
    const calls = serve({
      '/api/v1/auth/providers': () => json(PROVIDERS),
      '/api/v1/me': () => json({ email: 'attacker@example.com' }),
    })
    const router = renderAt('/signin#key=pq_attackers_key&next=%2Fportfolio')

    expect(await screen.findByRole('alert')).toHaveTextContent('was not started in this browser')
    expect(router.state.location.pathname).toBe('/signin')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
    expect(calls.some((call) => call.authorization === 'Bearer pq_attackers_key')).toBe(false)
  })

  it('does not show a message written into a link', async () => {
    serve({ '/api/v1/auth/providers': () => json(PROVIDERS) })
    renderAt('/signin#error=Your+account+is+locked.+Call+555-0100.')

    expect(await screen.findByRole('alert')).toHaveTextContent('was not started in this browser')
    expect(screen.queryByText(/555-0100/)).toBeNull()
  })

  it('does not trust a sign-in started long ago', () => {
    markSignInStarted()
    expect(signInStartedRecently()).toBe(true)
    expect(signInStartedRecently(Date.now() + 16 * 60 * 1000)).toBe(false)
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

describe('deleting the account', () => {
  const ACCOUNT = {
    id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
    email: 'me@example.com',
    created_at: '2026-10-01T00:00:00Z',
    anthropic: { configured: false, hint: null },
    fmp: { configured: false, hint: null },
    alpha_vantage: { configured: false, hint: null },
    finnhub: { configured: false, hint: null },
    limits: { ai_requests_per_hour: null, ai_requests_per_day: null },
    usage: { last_hour: 0, last_day: 0 },
    rate_limit_per_minute: 120,
    market_data_fallback: false,
    sec_filings: true,
  }

  it('asks for the email, deletes, and signs out', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const calls = serve({
      '/api/v1/me': (init) =>
        init?.method === 'DELETE' ? new Response(null, { status: 204 }) : json(ACCOUNT),
      '/api/v1/auth/signout': () => new Response(null, { status: 204 }),
      '/api/v1/auth/providers': () => json([]),
    })
    const router = renderAt('/settings')

    const button = await screen.findByRole('button', { name: 'Delete account' })
    expect(button).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Type me@example.com to confirm/), 'ME@example.com')
    expect(button).toBeEnabled()
    await userEvent.click(button)

    // Nothing is deleted until the warning is confirmed.
    const dialog = await screen.findByRole('alertdialog', { name: 'Delete your account?' })
    expect(dialog).toHaveTextContent('cannot be undone')
    expect(dialog).toHaveTextContent('Your saved backtests')
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete my account' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/signin'))
    const deletion = calls.find((call) => call.method === 'DELETE')
    expect(deletion).toEqual({
      path: '/api/v1/me',
      method: 'DELETE',
      authorization: 'Bearer pq_mine',
    })
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
  })

  it('keeps the account when the warning is cancelled', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const calls = serve({
      '/api/v1/me': () => json(ACCOUNT),
      '/api/v1/auth/providers': () => json([]),
    })
    renderAt('/settings')

    const button = await screen.findByRole('button', { name: 'Delete account' })
    await userEvent.type(screen.getByLabelText(/Type me@example.com to confirm/), 'me@example.com')
    await userEvent.click(button)
    await screen.findByRole('alertdialog')
    await userEvent.keyboard('{Escape}')

    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(calls.some((call) => call.method === 'DELETE')).toBe(false)
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_mine')
  })
})

describe('admin settings', () => {
  const ACCOUNT = {
    id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
    email: 'me@example.com',
    created_at: '2026-10-01T00:00:00Z',
    anthropic: { configured: false, hint: null },
    fmp: { configured: false, hint: null },
    alpha_vantage: { configured: false, hint: null },
    finnhub: { configured: false, hint: null },
    limits: { ai_requests_per_hour: null, ai_requests_per_day: null },
    usage: { last_hour: 0, last_day: 0 },
    rate_limit_per_minute: 120,
    market_data_fallback: false,
    sec_filings: true,
  }

  it('are hidden when the server sends none', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    serve({ '/api/v1/me': () => json(ACCOUNT) })
    renderAt('/settings')

    expect(await screen.findByRole('heading', { name: 'Delete account' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Admin settings' })).toBeNull()
    expect(screen.queryByText(/Fall back to yfinance/)).toBeNull()
  })

  const ADMIN = {
    yfinance_fallback: false,
    yfinance_fallback_for_everyone: false,
    yfinance_fallback_from_server: false,
  }

  it('turn the yfinance fallback on for the admin alone', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const admin = { ...ACCOUNT, admin: ADMIN }
    const sent: unknown[] = []
    const calls = serve({
      '/api/v1/me': () => json(admin),
      '/api/v1/me/admin': (init) => {
        sent.push(JSON.parse(String(init?.body)))
        return json({ ...admin, admin: { ...ADMIN, yfinance_fallback: true } })
      },
    })
    renderAt('/settings')

    const mine = await screen.findByRole('checkbox', { name: /for my own requests/ })
    expect(mine).not.toBeChecked()
    await userEvent.click(mine)

    await waitFor(() => expect(mine).toBeChecked())
    expect(screen.getByRole('checkbox', { name: /for everyone/ })).not.toBeChecked()
    expect(sent).toEqual([{ yfinance_fallback: true }])
    expect(calls.find((call) => call.path === '/api/v1/me/admin')).toEqual({
      path: '/api/v1/me/admin',
      method: 'PUT',
      authorization: 'Bearer pq_mine',
    })
  })

  it('turn the yfinance fallback on for everyone', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const admin = { ...ACCOUNT, admin: ADMIN }
    const sent: unknown[] = []
    serve({
      '/api/v1/me': () => json(admin),
      '/api/v1/me/admin': (init) => {
        sent.push(JSON.parse(String(init?.body)))
        return json({ ...admin, admin: { ...ADMIN, yfinance_fallback_for_everyone: true } })
      },
    })
    renderAt('/settings')

    const everyone = await screen.findByRole('checkbox', { name: /for everyone/ })
    await userEvent.click(everyone)

    await waitFor(() => expect(everyone).toBeChecked())
    expect(sent).toEqual([{ yfinance_fallback_for_everyone: true }])
    // Everyone includes the admin, so their own switch shows on and is locked.
    expect(screen.getByRole('checkbox', { name: /for my own requests/ })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: /for my own requests/ })).toBeDisabled()
  })

  it('say when the server forces the fallback on', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const admin = { ...ACCOUNT, admin: { ...ADMIN, yfinance_fallback_from_server: true } }
    serve({ '/api/v1/me': () => json(admin) })
    renderAt('/settings')

    expect(await screen.findByText(/YFINANCE_FALLBACK is on/)).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: /for everyone/ })).toBeDisabled()
  })
})

describe('provider keys', () => {
  const ACCOUNT = {
    id: '6f9619ff-8b86-d011-b42d-00c04fc964ff',
    email: 'me@example.com',
    created_at: '2026-10-01T00:00:00Z',
    anthropic: { configured: false, hint: null },
    fmp: { configured: false, hint: null },
    alpha_vantage: { configured: false, hint: null },
    finnhub: { configured: false, hint: null },
    limits: { ai_requests_per_hour: null, ai_requests_per_day: null },
    usage: { last_hour: 0, last_day: 0 },
    rate_limit_per_minute: 120,
    market_data_fallback: false,
    sec_filings: true,
  }

  afterEach(() => clearProviderKeys())

  /** Answers /me as the server does: reporting the keys the request carried. */
  const me = (init: RequestInit | undefined) => {
    const headers = (init?.headers ?? {}) as Record<string, string>
    const fmp = headers['X-FMP-Key']
    return json({
      ...ACCOUNT,
      fmp: fmp ? { configured: true, hint: `…${fmp.slice(-4)}` } : ACCOUNT.fmp,
    })
  }

  it('are kept in this browser and sent with requests, never stored on the server', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    const calls = serve({ '/api/v1/me': me })
    renderAt('/settings')

    const input = await screen.findByLabelText('Financial Modeling Prep API key')
    await userEvent.type(input, 'fmp-secret-5678')
    const form = input.closest('form') as HTMLFormElement
    await userEvent.click(within(form).getByRole('button', { name: 'Save' }))

    expect(await within(form).findByText(/In this browser · ends …5678/)).toBeInTheDocument()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    expect(window.localStorage.getItem('pyqxel.providerKeys')).toContain('fmp-secret-5678')
  })

  it('are forgotten on signing out', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_mine')
    setProviderKey('fmp', 'fmp-secret-5678')
    serve({
      '/api/v1/me': me,
      '/api/v1/auth/signout': () => new Response(null, { status: 204 }),
      '/api/v1/auth/providers': () => json([]),
    })
    renderAt('/settings')

    await userEvent.click(await screen.findByRole('button', { name: /Sign out/ }))

    await waitFor(() => expect(window.localStorage.getItem('pyqxel.providerKeys')).toBeNull())
    expect(providerKeyHeaders()).toEqual({})
  })
})
