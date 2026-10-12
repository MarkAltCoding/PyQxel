import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
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

describe('sign-in', () => {
  it('sends visitors without a key to sign in, then back where they were going', async () => {
    const fetch = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => new Response(JSON.stringify({ email: 'a@b.c' })))
    const router = renderAt('/screener')

    expect(router.state.location.pathname).toBe('/signin')
    await userEvent.type(screen.getByLabelText('API key'), 'pq_good')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(router.state.location.pathname).toBe('/screener')
    const [, init] = fetch.mock.calls[0]!
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer pq_good')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_good')
  })

  it('explains a rejected key and stays on the sign-in page', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Invalid API key.' }), { status: 401 }),
    )
    const router = renderAt('/')

    await userEvent.type(screen.getByLabelText('API key'), 'pq_bad')
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid API key.')
    expect(router.state.location.pathname).toBe('/signin')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
  })
})
