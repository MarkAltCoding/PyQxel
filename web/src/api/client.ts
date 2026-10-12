// A small fetch wrapper: sends the API key, encodes JSON, and turns error responses
// into ApiError with the server's message.

let apiKey: string | null = null
let onUnauthorized: (() => void) | null = null

/** Sets the key sent with every request, and what to do when the server rejects it. */
export function configureClient(key: string | null, unauthorized?: () => void): void {
  apiKey = key
  onUnauthorized = unauthorized ?? null
}

export class ApiError extends Error {
  readonly status: number
  readonly retryAfter: number | null

  constructor(status: number, message: string, retryAfter: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfter = retryAfter
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  query?: Query
  body?: unknown
  signal?: AbortSignal
  /** Overrides the configured key, e.g. to check a key before keeping it. */
  key?: string
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams()
  for (const [name, value] of Object.entries(query ?? {})) {
    if (value !== null && value !== undefined && value !== '') params.set(name, String(value))
  }
  const search = params.toString()
  return search ? `${path}?${search}` : path
}

/** Reads FastAPI's error body: a string `detail`, or a list of validation errors. */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      const detail = (body as { detail: unknown }).detail
      if (typeof detail === 'string') return detail
      if (Array.isArray(detail)) {
        return detail
          .map((item: { loc?: unknown[]; msg?: string }) => {
            const where = (item.loc ?? []).filter((part) => part !== 'body').join('.')
            return where ? `${where}: ${item.msg ?? ''}` : (item.msg ?? '')
          })
          .join('; ')
      }
    }
  } catch {
    // Not JSON; fall through to the status text.
  }
  return response.statusText || `Request failed with status ${response.status}.`
}

export async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const key = options.key ?? apiKey
  if (key) headers.Authorization = `Bearer ${key}`
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'
  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  })
  if (!response.ok) {
    if (response.status === 401 && options.key === undefined) onUnauthorized?.()
    const retry = Number(response.headers.get('Retry-After'))
    throw new ApiError(
      response.status,
      await errorMessage(response),
      Number.isFinite(retry) && retry > 0 ? retry : null,
    )
  }
  return response
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
