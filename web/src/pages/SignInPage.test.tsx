import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../auth/AuthContext'
import { SetPasswordPage } from './SetPasswordPage'
import { SignInPage } from './SignInPage'

let calls: { path: string; body: unknown }[]
let answers: Record<string, () => Response>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  calls = []
  answers = {
    '/api/v1/auth/providers': () => json([{ name: 'github', label: 'GitHub' }]),
    '/api/v1/auth/password': () => json({ available: true, min_length: 12 }),
    '/api/v1/me': () => json({ email: 'me@example.com' }),
  }
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = new URL(String(input), 'http://localhost')
    calls.push({ path: url.pathname, body: init?.body ? JSON.parse(String(init.body)) : null })
    return (answers[url.pathname] ?? (() => json({ detail: 'Not found' }, 404)))()
  })
})

afterEach(() => vi.restoreAllMocks())

function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/signin" element={<SignInPage />} />
            <Route path="/signin/password" element={<SetPasswordPage />} />
            <Route path="/" element={<p>Home page</p>} />
          </Routes>
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  )
}

describe('signing in with an email and password', () => {
  it('signs in and keeps the key', async () => {
    answers['/api/v1/auth/password/sign-in'] = () =>
      json({ key: 'pq_from_password', email: 'me@example.com' })
    renderAt('/signin')

    await userEvent.type(await screen.findByLabelText('Email'), 'me@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'correct horse battery')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(calls.find((call) => call.path.endsWith('/sign-in'))?.body).toEqual({
      email: 'me@example.com',
      password: 'correct horse battery',
      app: false,
    })
    expect(window.localStorage.getItem('pyqxel.apiKey')).toContain('pq_from_password')
  })

  it('explains a refused password', async () => {
    answers['/api/v1/auth/password/sign-in'] = () =>
      json({ detail: 'That email and password do not match an account.' }, 401)
    renderAt('/signin')

    await userEvent.type(await screen.findByLabelText('Email'), 'me@example.com')
    await userEvent.type(screen.getByLabelText('Password'), 'wrong')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText(/do not match an account/)).toBeInTheDocument()
  })

  it('makes an account by asking for a link', async () => {
    answers['/api/v1/auth/password/link'] = () =>
      json({ message: 'We sent a link to new@example.com.' }, 202)
    renderAt('/signin')

    await userEvent.click(await screen.findByRole('button', { name: 'Make an account' }))
    await userEvent.type(screen.getByLabelText('Email'), 'new@example.com')
    await userEvent.click(screen.getByRole('button', { name: 'Email me a link' }))

    expect(await screen.findByText('We sent a link to new@example.com.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
  })

  it('hides the email form when the server cannot send email', async () => {
    answers['/api/v1/auth/password'] = () => json({ available: false, min_length: 12 })
    renderAt('/signin')

    expect(await screen.findByText('Continue with GitHub')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Forgot password?' })).not.toBeInTheDocument()
  })
})

describe('the page an emailed link opens', () => {
  it('names the account, sets the password, and signs in', async () => {
    answers['/api/v1/auth/password/check'] = () => json({ email: 'new@example.com' })
    answers['/api/v1/auth/password/set'] = () =>
      json({ key: 'pq_from_link', email: 'new@example.com' })
    window.location.hash = '#token=the-emailed-token-value'
    renderAt('/signin/password')

    expect(await screen.findByText('new@example.com')).toBeInTheDocument()
    expect(window.location.hash).toBe('')
    const first = screen.getByLabelText(/^New password/)
    const second = screen.getByLabelText(/^Type it again/)
    await userEvent.type(first, 'a long enough password')
    await userEvent.type(second, 'a long enough passwor')
    expect(screen.getByText(/not the same yet/)).toBeInTheDocument()
    await userEvent.type(second, 'd')
    await userEvent.click(screen.getByRole('button', { name: 'Save password and sign in' }))

    expect(await screen.findByText('Home page')).toBeInTheDocument()
    expect(calls.find((call) => call.path.endsWith('/set'))?.body).toEqual({
      token: 'the-emailed-token-value',
      password: 'a long enough password',
    })
  })

  it('says what to do without a working link', async () => {
    answers['/api/v1/auth/password/check'] = () =>
      json({ detail: 'That link has expired or was already used. Ask for a new one.' }, 400)
    window.location.hash = '#token=old-token-value-old-token'
    renderAt('/signin/password')

    expect(await screen.findByText(/expired or was already used/)).toBeInTheDocument()
    await waitFor(() =>
      expect(screen.getByRole('link', { name: 'Back to sign in' })).toBeInTheDocument(),
    )
  })
})
