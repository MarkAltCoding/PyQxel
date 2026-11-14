import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { follows, reconcile, startSavedState, stopSavedState, flushSavedState } from './savedState'
import { readStored, writeStored } from './storage'

/** What the account holds, as the fake server keeps it, and every PUT it received. */
let account: Record<string, string>
let puts: Record<string, string | null>[]
let online: boolean

beforeEach(() => {
  account = {}
  puts = []
  online = true
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_input, init) => {
    if (!online) throw new TypeError('Failed to fetch')
    if (init?.method === 'PUT') {
      const { items } = JSON.parse(String(init.body)) as { items: Record<string, string | null> }
      puts.push(items)
      for (const [key, value] of Object.entries(items)) {
        if (value === null) delete account[key]
        else account[key] = value
      }
    }
    return new Response(JSON.stringify({ account: 'user-1', items: account }))
  })
})

afterEach(async () => {
  await stopSavedState(false)
  vi.restoreAllMocks()
})

describe('what follows the account', () => {
  it('is everything entered or chosen, and never a key or a sign-in', () => {
    for (const key of [
      'pyqxel.strategyDraft.python.one',
      'pyqxel.screen',
      'pyqxel.reviewProfile',
      'pyqxel.picksProfile',
      'pyqxel.backtestUniverse',
      'pyqxel.theme',
    ]) {
      expect(follows(key), key).toBe(true)
    }
    for (const key of [
      'pyqxel.apiKey',
      'pyqxel.providerKeys',
      'pyqxel.appSignIn',
      'pyqxel.signInStarted',
      'pyqxel.adviceNoticeAccepted',
      'pyqxel.exportedPrompt.picks',
      'pyqxel.saved.owner',
      'someone-elses-key',
    ]) {
      expect(follows(key), key).toBe(false)
    }
  })
})

describe('opening the app', () => {
  it('brings in what the account saved, as in a new browser or after a reinstall', async () => {
    account = { 'pyqxel.screen': '{"universe":"all"}', 'pyqxel.strategyDraft.python.one': 'code' }

    await startSavedState()

    expect(readStored('pyqxel.screen')).toBe('{"universe":"all"}')
    expect(readStored('pyqxel.strategyDraft.python.one')).toBe('code')
    expect(puts).toEqual([])
  })

  it('sends up what this browser held before accounts kept it, once', async () => {
    window.localStorage.setItem('pyqxel.strategyDraft.python.one', 'my strategy')
    window.localStorage.setItem('pyqxel.apiKey', 'secret')
    account = { 'pyqxel.screen': 'from the account' }

    await startSavedState()

    expect(puts).toEqual([{ 'pyqxel.strategyDraft.python.one': 'my strategy' }])
    expect(account['pyqxel.strategyDraft.python.one']).toBe('my strategy')
    expect(readStored('pyqxel.screen')).toBe('from the account')
  })

  it('takes the account’s value over this browser’s, and drops what was removed elsewhere', async () => {
    await reconcile('user-1', { 'pyqxel.fractionalShares': 'off', 'pyqxel.theme': 'dark' })
    // Another device turned fractional shares back on (which forgets the value) and
    // changed the theme.
    await reconcile('user-1', { 'pyqxel.theme': 'light' })

    expect(readStored('pyqxel.fractionalShares')).toBeNull()
    expect(readStored('pyqxel.theme')).toBe('light')
    expect(puts).toEqual([])
  })

  it('drops another account’s values instead of saving them to this one', async () => {
    await reconcile('user-2', { 'pyqxel.screen': 'theirs' })
    window.localStorage.setItem('pyqxel.reviewProfile', 'theirs too')

    await reconcile('user-1', { 'pyqxel.theme': 'dark' })

    expect(readStored('pyqxel.screen')).toBeNull()
    expect(readStored('pyqxel.reviewProfile')).toBeNull()
    expect(readStored('pyqxel.theme')).toBe('dark')
    expect(puts).toEqual([])
  })

  it('goes on with this browser’s values when the account cannot be reached', async () => {
    window.localStorage.setItem('pyqxel.screen', 'local')
    online = false

    await startSavedState()

    expect(readStored('pyqxel.screen')).toBe('local')
  })
})

describe('writing', () => {
  it('saves each value to the account, and forgets one set to nothing', async () => {
    await startSavedState()

    writeStored('pyqxel.screen', 'a')
    writeStored('pyqxel.screen', 'ab')
    writeStored('pyqxel.apiKey', 'secret')
    await flushSavedState()
    writeStored('pyqxel.screen', null)
    await flushSavedState()

    expect(puts).toEqual([{ 'pyqxel.screen': 'ab' }, { 'pyqxel.screen': null }])
    expect(account).toEqual({})
  })

  it('keeps what could not be sent, and sends it when the app next opens', async () => {
    await startSavedState()
    online = false
    writeStored('pyqxel.strategyDraft.python.one', 'written offline')
    await flushSavedState()
    await stopSavedState(false)
    online = true
    account = { 'pyqxel.strategyDraft.python.one': 'older, from the account' }

    await startSavedState()

    expect(readStored('pyqxel.strategyDraft.python.one')).toBe('written offline')
    expect(account['pyqxel.strategyDraft.python.one']).toBe('written offline')
  })

  it('is not sent before the app has loaded the account’s values', () => {
    writeStored('pyqxel.screen', 'a')

    expect(puts).toEqual([])
  })
})

describe('signing out', () => {
  it('saves one last time, then leaves nothing of the account in the browser', async () => {
    await startSavedState()
    writeStored('pyqxel.reviewProfile', '{"risk":"moderate"}')
    window.localStorage.setItem('pyqxel.apiKey', 'kept for the caller to remove')

    await stopSavedState(true)

    expect(account).toEqual({ 'pyqxel.reviewProfile': '{"risk":"moderate"}' })
    expect(readStored('pyqxel.reviewProfile')).toBeNull()
    expect(readStored('pyqxel.saved.owner')).toBeNull()
    expect(readStored('pyqxel.apiKey')).toBe('kept for the caller to remove')

    // Signing in again, here or anywhere, brings it back.
    await startSavedState()
    expect(readStored('pyqxel.reviewProfile')).toBe('{"risk":"moderate"}')
  })

  it('keeps the values when the sign-in only expired', async () => {
    await startSavedState()
    writeStored('pyqxel.screen', 'mine')
    await flushSavedState()

    await stopSavedState(false)

    expect(readStored('pyqxel.screen')).toBe('mine')
  })
})
