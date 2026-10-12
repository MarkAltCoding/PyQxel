import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError, buildUrl, configureClient } from './client'

const json = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })

afterEach(() => {
  vi.restoreAllMocks()
  configureClient(null)
})

describe('api client', () => {
  it('sends the API key and JSON body', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(200, { ok: true }))
    configureClient('pq_secret')

    await api('/api/v1/screener', { method: 'POST', body: { limit: 5 } })

    const [url, init] = fetch.mock.calls[0]!
    expect(url).toBe('/api/v1/screener')
    expect((init?.headers as Record<string, string>).Authorization).toBe('Bearer pq_secret')
    expect(init?.body).toBe('{"limit":5}')
  })

  it('turns FastAPI errors into ApiError with the detail and Retry-After', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json(429, { detail: 'Hourly AI limit reached.' }, { 'Retry-After': '3600' }),
    )

    const error = await api('/api/v1/x').catch((caught: unknown) => caught)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      status: 429,
      message: 'Hourly AI limit reached.',
      retryAfter: 3600,
    })
  })

  it('summarizes validation errors without the body prefix', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      json(422, { detail: [{ loc: ['body', 'holdings', 0, 'shares'], msg: 'must be > 0' }] }),
    )

    await expect(api('/api/v1/x')).rejects.toThrow('holdings.0.shares: must be > 0')
  })

  it('signs out when the server rejects the key', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(json(401, { detail: 'Invalid API key.' }))
    const signOut = vi.fn()
    configureClient('pq_revoked', signOut)

    await expect(api('/api/v1/me')).rejects.toThrow('Invalid API key.')
    expect(signOut).toHaveBeenCalledOnce()
  })

  it('leaves out empty query parameters', () => {
    expect(buildUrl('/a', { q: 'msft', sector: '', limit: 8, x: null })).toBe('/a?q=msft&limit=8')
  })
})
