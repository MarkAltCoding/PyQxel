import { afterEach, describe, expect, it } from 'vitest'
import { loadSecureStorage, resetSecureStorage } from './secureStorage'
import { readStored, writeStored } from './storage'

/** A native side that keeps secrets in a map, as the Keychain or the file would. */
function fakeNative(initial: Record<string, string> = {}, failWrites = false) {
  const kept = new Map(Object.entries(initial))
  const invoke = async <T>(command: string, args?: Record<string, unknown>): Promise<T> => {
    const name = String(args?.name)
    if (command === 'secret_get') return (kept.get(name) ?? null) as T
    if (failWrites) throw new Error('Keychain unavailable')
    if (command === 'secret_set') kept.set(name, String(args?.value))
    if (command === 'secret_delete') kept.delete(name)
    return undefined as T
  }
  return { kept, invoke }
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0))

afterEach(() => resetSecureStorage())

describe('secure storage in the native app', () => {
  it('moves what an earlier version left in web storage to the native side', async () => {
    window.localStorage.setItem('pyqxel.apiKey', 'pq_left_behind')
    window.localStorage.setItem('pyqxel.theme', 'dark')
    const native = fakeNative()

    await loadSecureStorage(native.invoke)

    expect(native.kept.get('pyqxel.apiKey')).toBe('pq_left_behind')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBeNull()
    expect(readStored('pyqxel.apiKey')).toBe('pq_left_behind')
    expect(readStored('pyqxel.theme')).toBe('dark')
  })

  it('reads and writes the keys on the native side, never in web storage', async () => {
    const native = fakeNative({ 'pyqxel.apiKey': 'pq_kept' })
    await loadSecureStorage(native.invoke)

    expect(readStored('pyqxel.apiKey')).toBe('pq_kept')
    writeStored('pyqxel.providerKeys', '{"fmp":"secret"}')
    writeStored('pyqxel.apiKey', null)
    await settle()

    expect(native.kept.get('pyqxel.providerKeys')).toBe('{"fmp":"secret"}')
    expect(native.kept.has('pyqxel.apiKey')).toBe(false)
    expect(window.localStorage.getItem('pyqxel.providerKeys')).toBeNull()
    expect(readStored('pyqxel.apiKey')).toBeNull()
  })

  it('falls back to web storage when the native side refuses a write', async () => {
    const native = fakeNative({}, true)
    await loadSecureStorage(native.invoke)

    writeStored('pyqxel.apiKey', 'pq_somewhere')
    await settle()

    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_somewhere')
    expect(readStored('pyqxel.apiKey')).toBe('pq_somewhere')
  })

  it('leaves a browser on web storage', async () => {
    await loadSecureStorage()
    writeStored('pyqxel.apiKey', 'pq_browser')
    expect(window.localStorage.getItem('pyqxel.apiKey')).toBe('pq_browser')
  })
})
